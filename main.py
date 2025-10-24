import json
import re
from typing import List, Optional, Dict, Tuple

import uvicorn
from fastapi import FastAPI, Request, Depends
from pydantic import BaseModel
from contextlib import asynccontextmanager
import pymorphy3


# Инициализируем морфологический анализатор
morph = pymorphy3.MorphAnalyzer()


def normalize_text(text: str) -> str:
    """
    Нормализует текст, приводя все слова к начальной форме.
    
    Args:
        text: Исходный текст
        
    Returns:
        Нормализованный текст с словами в начальной форме
    """
    words = text.split()
    normalized_words = []
    
    for word in words:
        clean_word = re.sub(r'[^\w]', '', word)
        if clean_word:
            parsed = morph.parse(clean_word)[0]
            normalized_words.append(parsed.normal_form)
        else:
            normalized_words.append(word)
    
    return ' '.join(normalized_words)


def generate_word_forms(word: str) -> set:
    """
    Генерирует все возможные формы слова (падежи, числа) через pymorphy3.
    
    Args:
        word: Слово в начальной форме
        
    Returns:
        Множество всех форм слова
    """
    parsed = morph.parse(word)[0]
    forms = {word.lower()}
    
    # Генерируем все формы слова
    for form in parsed.lexeme:
        forms.add(form.word.lower())
    
    return forms


def create_flexible_pattern(alias: str) -> str:
    """
    Создает гибкий паттерн для алиаса с учетом склонений.
    
    Args:
        alias: Алиас закона в нижнем регистре
        
    Returns:
        Регулярное выражение (строка) с учетом склонений
    """
    # Разбиваем алиас на токены (слова и не-слова)
    tokens = re.findall(r'[а-яёa-z]+|[^а-яёa-z]+', alias, re.IGNORECASE)
    
    pattern_parts = []
    
    for token in tokens:
        if re.match(r'^[а-яёa-z]+$', token, re.IGNORECASE):
            # Это слово - генерируем все формы
            word_forms = generate_word_forms(token)
            
            # Если слово длинное (>3 символов) и имеет разные формы, создаем альтернативы
            if len(word_forms) > 1 and len(token) > 3:
                # Сортируем формы по длине (от длинных к коротким) для более точного матчинга
                sorted_forms = sorted(word_forms, key=len, reverse=True)
                escaped_forms = [re.escape(form) for form in sorted_forms]
                pattern_parts.append(f"(?:{'|'.join(escaped_forms)})")
            else:
                # Короткое слово или нет форм - оставляем как есть
                pattern_parts.append(re.escape(token))
        else:
            # Это не слово (пробелы, знаки препинания) - делаем гибким
            # Пробелы могут быть опциональными или множественными
            if token.strip() == '':
                pattern_parts.append(r'\s+')
            else:
                pattern_parts.append(re.escape(token))
    
    pattern = ''.join(pattern_parts)
    
    # Добавляем границы слов в начале и конце, если нужно
    if alias and re.match(r'[\wа-яёА-ЯЁ]', alias[0]):
        pattern = r'\b' + pattern
    if alias and re.match(r'[\wа-яёА-ЯЁ]', alias[-1]):
        pattern = pattern + r'\b'
    
    return pattern


def load_law_aliases_with_morphology():
    """
    Загружает law_aliases.json и создает индекс с нормализованными формами.
    ОПТИМИЗАЦИЯ: предкомпилирует все регулярные выражения один раз.
    Учитывает склонения слов в алиасах.
    
    Returns:
        Tuple[Dict, List]: (нормализованный_алиас -> [(оригинал, law_id)], все алиасы отсортированные)
    """
    with open('law_aliases.json', 'r', encoding='utf-8') as f:
        law_aliases = json.load(f)
    
    normalized_index = {}
    all_aliases = []
    
    for law_id, aliases in law_aliases.items():
        for alias in aliases:
            alias_lower = alias.lower()
            normalized = normalize_text(alias_lower)
            
            if normalized not in normalized_index:
                normalized_index[normalized] = []
            normalized_index[normalized].append((alias_lower, law_id))
            
            # ОПТИМИЗАЦИЯ: создаем гибкий паттерн с учетом склонений
            flexible_pattern_str = create_flexible_pattern(alias_lower)
            compiled_pattern = re.compile(flexible_pattern_str, re.IGNORECASE)
            
            # Также создаем точный паттерн для быстрого матчинга (без склонений)
            escaped_alias = re.escape(alias_lower)
            if re.match(r'[\w]', alias_lower):
                exact_pattern_str = r'\b' + escaped_alias
            else:
                exact_pattern_str = escaped_alias
            if re.search(r'[\w]$', alias_lower):
                exact_pattern_str = exact_pattern_str + r'\b'
            
            exact_compiled_pattern = re.compile(exact_pattern_str, re.IGNORECASE)
            
            all_aliases.append({
                'original': alias_lower,
                'normalized': normalized,
                'law_id': law_id,
                'length': len(alias_lower),
                'word_count': len(alias_lower.split()),
                'compiled_pattern': compiled_pattern,  # Гибкий паттерн с учетом склонений
                'exact_pattern': exact_compiled_pattern  # Точный паттерн для быстрого поиска
            })
    
    all_aliases.sort(key=lambda x: x['length'], reverse=True)
    
    return normalized_index, all_aliases


def find_law_in_text(text: str, normalized_index: Dict, all_aliases: List[Dict]) -> Optional[str]:
    """
    Ищет упоминание кодекса в тексте с учетом склонений.
    
    Args:
        text: Текст для поиска
        normalized_index: Индекс нормализованных форм
        all_aliases: Список всех алиасов
        
    Returns:
        law_id или None
    """
    text_lower = text.lower()
    
    # Извлекаем номера документов из текста (например, №474, №201-рп)
    numbers_in_text = re.findall(r'№\s*(\d+(?:[-./]\S*)?)', text)
    
    # Извлекаем тип документа из текста (Указ, Распоряжение, Постановление и т.д.)
    doc_type_in_text = None
    doc_type_patterns = {
        'указ': r'указ[аеуыои]?\b',
        'распоряжен': r'распоряжен[иеюя]+\b',
        'постановлен': r'постановлен[иеюя]+\b',
        'приказ': r'приказ[аеуыои]?\b',
        'закон': r'закон[аеуыои]?\b'
    }
    
    for base_type, pattern in doc_type_patterns.items():
        if re.search(pattern, text_lower):
            doc_type_in_text = base_type
            break
    
    # Если в тексте нет номера, собираем кандидатов по ключевым словам
    best_match_without_number = None
    best_match_score = 0
    
    # Сначала пробуем прямое совпадение (быстрее и точнее)
    # ОПТИМИЗАЦИЯ: используем предкомпилированные регулярные выражения
    for alias_data in all_aliases:
        alias_lower = alias_data['original']
        
        # Если в тексте есть номер документа, проверяем совместимость с алиасом
        if numbers_in_text:
            # Извлекаем номера из алиаса
            alias_numbers = re.findall(r'№\s*(\d+(?:[-./]\S*)?)', alias_lower)
            
            # Если у алиаса есть номер, он должен совпадать с номером в тексте
            if alias_numbers:
                # Нормализуем номера (убираем точки и другие знаки препинания в конце)
                normalized_text_numbers = [num.rstrip('.,;:!?') for num in numbers_in_text]
                normalized_alias_numbers = [num.rstrip('.,;:!?') for num in alias_numbers]
                has_matching_number = any(alias_num in normalized_text_numbers for alias_num in normalized_alias_numbers)
                if not has_matching_number:
                    # Пропускаем этот алиас, так как номер не совпадает
                    continue
                else:
                    # Если номер совпадает и тип документа совпадает,
                    # проверяем наличие ключевых слов (более гибко, с учетом склонений)
                    if doc_type_in_text and doc_type_in_text in alias_lower:
                        # Извлекаем ключевые слова из алиаса (слова длиннее 3 символов, буквенные)
                        alias_words = re.findall(r'\b[а-яёa-z]{4,}\b', alias_lower)
                        if alias_words:
                            # Проверяем, что большинство ключевых слов присутствуют в тексте
                            matching_count = sum(1 for word in alias_words if word in text_lower)
                            # Если хотя бы 60% ключевых слов совпадают, считаем это совпадением
                            if matching_count >= len(alias_words) * 0.6:
                                return alias_data['law_id']
        
        # Если в тексте есть тип документа, проверяем совместимость с алиасом
        if doc_type_in_text:
            # Проверяем, что алиас содержит тот же тип документа
            if doc_type_in_text not in alias_lower:
                # Пропускаем этот алиас, так как тип документа не совпадает
                continue
            
            # Если в тексте НЕТ номера, собираем кандидатов по ключевым словам (для учета склонений)
            if not numbers_in_text:
                alias_words = re.findall(r'\b[а-яёa-z]{4,}\b', alias_lower)
                if alias_words:
                    matching_count = sum(1 for word in alias_words if word in text_lower)
                    match_ratio = matching_count / len(alias_words)
                    # Сохраняем лучший результат (с максимальным процентом совпадения)
                    if match_ratio > best_match_score and match_ratio >= 0.7:
                        best_match_score = match_ratio
                        best_match_without_number = alias_data['law_id']
        
        # Сначала пробуем точное совпадение (быстрее)
        if alias_data['exact_pattern'].search(text_lower):
            return alias_data['law_id']
        
        # Если точного совпадения нет, пробуем гибкое (с учетом склонений)
        if alias_data['compiled_pattern'].search(text_lower):
            return alias_data['law_id']
    
    # Если нашли подходящий алиас по ключевым словам (для случая без номера в тексте)
    if best_match_without_number:
        return best_match_without_number
    
    # Если прямого совпадения нет, пробуем с нормализацией
    # Разбиваем текст на фразы (последовательности слов)
    word_sequences = re.finditer(r'[а-яёА-ЯЁ\w\s]+', text_lower)
    
    for seq_match in word_sequences:
        sequence = seq_match.group()
        words = sequence.split()
        
        max_window = min(10, len(words))
        
        for window_size in range(max_window, 0, -1):
            for i in range(len(words) - window_size + 1):
                window = words[i:i + window_size]
                phrase = ' '.join(window)
                
                normalized_phrase = normalize_text(phrase)
                
                if normalized_phrase in normalized_index:
                    # Дополнительная проверка для коротких алиасов
                    matches = normalized_index[normalized_phrase]
                    
                    for original_alias, law_id in matches:
                        # Если алиас короткий (как "НК", "ГК"), требуем точного совпадения
                        if len(original_alias) <= 3:
                            # Проверяем, что фраза в тексте выглядит как аббревиатура
                            if re.search(r'\b' + re.escape(phrase.upper()) + r'\b', text.upper()):
                                return law_id
                        else:
                            return law_id
    
    return None


def parse_legal_reference_v2(text: str, normalized_index: Dict, all_aliases: List[Dict]) -> List[Dict[str, Optional[str]]]:
    """
    Парсит юридический текст и извлекает все упоминания статей, пунктов и подпунктов.
    Версия с поддержкой склонений через pymorphy3 и множественных ссылок.
    
    Args:
        text: Текст с упоминаниями статей, пунктов и/или подпунктов
        normalized_index: Индекс нормализованных форм
        all_aliases: Список всех алиасов
        
    Returns:
        Список словарей с полями law_id, article, point_article, subpoint_article
    """
    results = []
    
    text_lower = text.lower().strip()
    text_stripped = text.strip()
    
    # Поиск law_id с учетом склонений
    law_id = find_law_in_text(text_lower, normalized_index, all_aliases)
    
    # Паттерны для поиска статей с перечислениями
    article_list_patterns = [
        r'стат(?:ь(?:ями|ях|ям|ёй|ей|[яиею])|ей)\s+((?:\d+(?:[.-]\d+)*(?:\s*,\s*|\s+и\s+|\s+или\s+)?)+)',
        r'ст\.?\s*((?:\d+(?:[.-]\d+)*(?:\s*,\s*|\s+и\s+|\s+или\s+)?)+)',
    ]
    
    # Паттерны для поиска пунктов с перечислениями
    point_list_patterns = [
        r'(?<!под)пункт[аеуыои]?\s+((?:(?:\d+[а-яА-Я]?|[а-яА-Я])(?:\s*,\s*(?:(?:и|или)\s+)?|\s+(?:и|или)\s+)?)+)',
        r'(?<![а-яА-Я])п\.?\s+((?:(?:\d+[а-яА-Я]?|[а-яА-Я])(?:\s*,\s*(?:(?:и|или)\s+)?|\s+(?:и|или)\s+)?)+)',
    ]
    
    # Паттерны для поиска подпунктов с перечислениями
    subpoint_list_patterns = [
        r'подпункт[аеуыои]?\s+((?:(?:[а-яА-Я](?!\.)\d*|\d+)(?:\s*,\s*(?:(?:и|или)\s+)?|\s+(?:и|или)\s+)?)+)',
        r'подп\.?\s+((?:(?:[а-яА-Я](?!\.)\d*|\d+)(?:\s*,\s*(?:(?:и|или)\s+)?|\s+(?:и|или)\s+)?)+)',
        r'пп\.?\s+((?:(?:[а-яА-Я](?!\.)\d*|\d+)(?:\s*,\s*(?:(?:и|или)\s+)?|\s+(?:и|или)\s+)?)+)',
    ]
    
    # Функция для разбора перечисления
    def parse_enumeration(enum_str: str, pattern: str) -> List[str]:
        """Разбивает строку перечисления на отдельные элементы, сохраняя регистр"""
        # Заменяем союзы на запятые для единообразия (case-insensitive)
        enum_str = re.sub(r'\s+и\s+', ',', enum_str, flags=re.IGNORECASE)
        enum_str = re.sub(r'\s+или\s+', ',', enum_str, flags=re.IGNORECASE)
        # Разбиваем по запятым
        items = [item.strip() for item in enum_str.split(',')]
        # Фильтруем пустые и оставляем только те, что соответствуют паттерну
        return [item for item in items if item and re.match(pattern, item, re.IGNORECASE)]
    
    # Поиск всех статей
    articles_found = []
    for pattern in article_list_patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            # Извлекаем из оригинального текста, сохраняя регистр
            enum_str = text_stripped[match.start(1):match.end(1)]
            articles = parse_enumeration(enum_str, r'^\d+(?:[.-]\d+)*$')
            articles_found.extend(articles)
    
    # Поиск всех пунктов
    points_found = []
    for pattern in point_list_patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            # Извлекаем из оригинального текста, сохраняя регистр
            enum_str = text_stripped[match.start(1):match.end(1)]
            points = parse_enumeration(enum_str, r'^(?:\d+[а-яА-Я]?|[а-яА-Я])$')
            # Фильтруем "п" - это сокращение для слова "пункт", а не номер пункта
            points = [p for p in points if p.lower() != 'п']
            points_found.extend(points)
    
    # Поиск всех подпунктов
    subpoints_found = []
    for pattern in subpoint_list_patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            # Извлекаем из оригинального текста, сохраняя регистр
            enum_str = text_stripped[match.start(1):match.end(1)]
            # Подпункты: буква (я), буква+цифры (я1), или просто цифры (26)
            subpoints = parse_enumeration(enum_str, r'^(?:[а-яА-Я]\d*|\d+)$')
            # Фильтруем "п" и "подп" - это сокращения для слова "пункт/подпункт", а не номера
            subpoints = [s for s in subpoints if s.lower() not in ['п', 'подп', 'пп']]
            subpoints_found.extend(subpoints)
    
    # Конвертируем law_id из строки в число
    law_id_int = int(law_id) if law_id is not None else None
    
    # Формируем результаты на основе найденных элементов
    if subpoints_found and len(subpoints_found) > 1:
        # Есть перечисление подпунктов - создаем запись для каждого
        for subpoint in subpoints_found:
            result = {
                "law_id": law_id_int,
                "article": articles_found[0] if articles_found else None,
                "point_article": points_found[0] if points_found else None,
                "subpoint_article": subpoint
            }
            results.append(result)
    elif points_found and len(points_found) > 1:
        # Есть перечисление пунктов - создаем запись для каждого
        for point in points_found:
            result = {
                "law_id": law_id_int,
                "article": articles_found[0] if articles_found else None,
                "point_article": point,
                "subpoint_article": subpoints_found[0] if subpoints_found else None
            }
            results.append(result)
    elif articles_found and len(articles_found) > 1:
        # Есть перечисление статей - создаем запись для каждой
        for article in articles_found:
            result = {
                "law_id": law_id_int,
                "article": article,
                "point_article": points_found[0] if points_found else None,
                "subpoint_article": subpoints_found[0] if subpoints_found else None
            }
            results.append(result)
    elif articles_found or points_found or subpoints_found:
        # Единичное упоминание
        result = {
            "law_id": law_id_int,
            "article": articles_found[0] if articles_found else None,
            "point_article": points_found[0] if points_found else None,
            "subpoint_article": subpoints_found[0] if subpoints_found else None
        }
        results.append(result)
    else:
        # Если ничего не найдено, возвращаем пустую запись
        results.append({
            "law_id": law_id_int,
            "article": None,
            "point_article": None,
            "subpoint_article": None
        })
    
    return results


def parse_legal_reference_multi_law(text: str, normalized_index: Dict, all_aliases: List[Dict]) -> List[Dict[str, Optional[str]]]:
    """
    Парсит юридический текст, который может содержать ссылки на несколько разных законов.
    Использует улучшенный алгоритм для точной привязки статей к законам.
    
    Args:
        text: Текст с упоминаниями статей, пунктов и/или подпунктов из разных законов
        normalized_index: Индекс нормализованных форм
        all_aliases: Список всех алиасов
        
    Returns:
        Список словарей с полями law_id, article, point_article, subpoint_article
    """
    results = []
    text_lower = text.lower()
    
    # Шаг 1: Найти все упоминания законов в тексте
    law_mentions = []
    for alias_data in all_aliases:
        # Используем предкомпилированные паттерны
        for match in alias_data['exact_pattern'].finditer(text_lower):
            law_mentions.append({
                'law_id': alias_data['law_id'],
                'start': match.start(),
                'end': match.end(),
                'text': match.group()
            })
        
        # Если не нашли точного совпадения, пробуем гибкий паттерн
        if not law_mentions or alias_data['law_id'] not in [m['law_id'] for m in law_mentions]:
            for match in alias_data['compiled_pattern'].finditer(text_lower):
                # Проверяем, что это не дубликат
                if not any(m['start'] <= match.start() < m['end'] or m['start'] < match.end() <= m['end'] 
                          for m in law_mentions):
                    law_mentions.append({
                        'law_id': alias_data['law_id'],
                        'start': match.start(),
                        'end': match.end(),
                        'text': match.group()
                    })
    
    # Убираем дубликаты и сортируем по позиции
    seen = set()
    unique_mentions = []
    for mention in law_mentions:
        key = (mention['law_id'], mention['start'], mention['end'])
        if key not in seen:
            seen.add(key)
            unique_mentions.append(mention)
    
    law_mentions = sorted(unique_mentions, key=lambda x: x['start'])
    
    # Если не нашли ни одного закона, используем старую функцию
    if not law_mentions:
        return parse_legal_reference_v2(text, normalized_index, all_aliases)
    
    # Если нашли только один закон, используем старую функцию
    if len(law_mentions) == 1:
        return parse_legal_reference_v2(text, normalized_index, all_aliases)
    
    # Шаг 2: Найти все упоминания статей, пунктов, подпунктов в тексте
    legal_references = []
    
    # Паттерны для статей с пунктами и подпунктами
    combined_patterns = [
        # пп. X п. Y ст. Z
        r'(?:пп\.?\s+([а-яА-Я]\d*|\d+)\s+)?(?:п\.?\s+(\d+[а-яА-Я]?|[а-яА-Я])\s+)?ст\.?\s+(\d+(?:[.-]\d+)*)',
        # подп. X п. Y ст. Z
        r'(?:подп\.?\s+([а-яА-Я]\d*|\d+)\s+)?(?:п\.?\s+(\d+[а-яА-Я]?|[а-яА-Я])\s+)?стат(?:ь[яиею]|ей)\s+(\d+(?:[.-]\d+)*)',
    ]
    
    # Находим все совпадения с полными паттернами (со статьями)
    found_positions = set()
    for pattern in combined_patterns:
        for match in re.finditer(pattern, text_lower):
            subpoint = match.group(1) if match.lastindex >= 1 and match.group(1) else None
            point = match.group(2) if match.lastindex >= 2 and match.group(2) else None
            article = match.group(3) if match.lastindex >= 3 else None
            
            if article:
                legal_references.append({
                    'position': match.start(),
                    'article': article,
                    'point': point,
                    'subpoint': subpoint,
                    'law_id': None  # Будет определен позже
                })
                # Запоминаем позиции, чтобы не дублировать
                found_positions.add(match.start())
    
    # Паттерны для пунктов БЕЗ статей (например, "п. 10 АПК")
    point_only_patterns = [
        r'(?<!под)пункт[аеуыои]?\s+(\d+[а-яА-Я]?|[а-яА-Я])(?!\s+ст)',
        r'(?<![а-яА-Я])п\.?\s+(\d+[а-яА-Я]?|[а-яА-Я])(?!\s+ст)',
    ]
    
    for pattern in point_only_patterns:
        for match in re.finditer(pattern, text_lower):
            # Проверяем, что эта позиция еще не обработана
            if match.start() not in found_positions:
                point = match.group(1)
                if point and point.lower() not in ['п', 'пункт']:
                    legal_references.append({
                        'position': match.start(),
                        'article': None,
                        'point': point,
                        'subpoint': None,
                        'law_id': None  # Будет определен позже
                    })
                    found_positions.add(match.start())
    
    # Шаг 3: Привязать каждую статью к ближайшему закону
    for ref in legal_references:
        ref_pos = ref['position']
        
        # Ищем ближайший закон (слева или справа)
        min_distance = float('inf')
        closest_law = None
        
        for mention in law_mentions:
            # Расстояние до упоминания закона
            if ref_pos < mention['start']:
                # Статья находится слева от закона
                distance = mention['start'] - ref_pos
            else:
                # Статья находится справа от закона
                distance = ref_pos - mention['end']
            
            # Ограничиваем максимальное расстояние
            max_distance_before = 150  # Статья может быть максимум на 150 символов левее закона
            max_distance_after = 50    # Статья может быть максимум на 50 символов правее закона
            
            if ref_pos < mention['start']:
                if distance > max_distance_before:
                    continue
            else:
                if distance > max_distance_after:
                    continue
            
            if distance < min_distance:
                min_distance = distance
                closest_law = mention
        
        if closest_law:
            ref['law_id'] = closest_law['law_id']
    
    # Шаг 4: Формируем результаты
    for ref in legal_references:
        if ref['law_id'] is not None:
            results.append({
                'law_id': int(ref['law_id']),
                'article': ref['article'],
                'point_article': ref['point'],
                'subpoint_article': ref['subpoint']
            })
    
    # Убираем дубликаты
    unique_results = []
    seen_results = set()
    for result in results:
        key = (result['law_id'], result['article'], result['point_article'], result['subpoint_article'])
        if key not in seen_results:
            seen_results.add(key)
            unique_results.append(result)
    
    return unique_results if unique_results else results


class LawLink(BaseModel):
    law_id: Optional[int] = None
    article: Optional[str] = None
    point_article: Optional[str] = None
    subpoint_article: Optional[str] = None


class LinksResponse(BaseModel):
    links: List[LawLink]


class TextRequest(BaseModel):
    text: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 Сервис запускается...")
    print("📚 Загрузка law_aliases.json и создание индекса...")
    
    normalized_index, all_aliases = load_law_aliases_with_morphology()
    
    app.state.normalized_index = normalized_index
    app.state.all_aliases = all_aliases
    
    print(f"✅ Загружено {len(all_aliases)} алиасов")
    print("🎉 Сервис готов к работе!")
    
    yield
    
    # Shutdown
    print("🛑 Сервис завершается...")
    del normalized_index
    del all_aliases


def get_law_data(request: Request) -> Tuple[Dict, List[Dict]]:
    """Получает данные об алиасах законов из состояния приложения"""
    return request.app.state.normalized_index, request.app.state.all_aliases


app = FastAPI(
    title="Law Links Service",
    description="Cервис для выделения юридических ссылок из текста",
    version="1.0.0",
    lifespan=lifespan
)


@app.post("/detect")
async def get_law_links(
    data: TextRequest,
    request: Request,
    law_data: Tuple[Dict, List[Dict]] = Depends(get_law_data),
    ) -> LinksResponse:
    """
    Принимает текст и возвращает список юридических ссылок
    """
    normalized_index, all_aliases = law_data
    
    # Используем parse_legal_reference_multi_law для извлечения юридических ссылок
    results = parse_legal_reference_multi_law(data.text, normalized_index, all_aliases)
    
    # Конвертируем результаты в объекты LawLink
    links = [
        LawLink(
            law_id=result.get("law_id"),
            article=result.get("article"),
            point_article=result.get("point_article"),
            subpoint_article=result.get("subpoint_article")
        )
        for result in results
    ]
    
    return LinksResponse(links=links)


@app.get("/health")
async def health_check():
    """
    Проверка состояния сервиса
    """
    return {"status": "healthy"}



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8978)
