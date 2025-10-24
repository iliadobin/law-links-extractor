#!/bin/bash

# Скрипт для управления Docker контейнером сервиса юридических ссылок

case "$1" in
  build)
    echo "🔨 Сборка Docker образа..."
    docker build -t law-links-service .
    echo "✅ Образ собран!"
    ;;
  
  start)
    echo "🚀 Запуск контейнера..."
    docker run -d -p 8978:8978 --name law-links-container law-links-service
    echo "⏳ Ожидание запуска сервиса (может занять до 30 секунд)..."
    sleep 30
    echo "✅ Контейнер запущен!"
    echo "📡 Сервис доступен по адресу: http://localhost:8978/detect"
    echo "📚 Swagger UI: http://localhost:8978/docs"
    ;;
  
  stop)
    echo "🛑 Остановка контейнера..."
    docker stop law-links-container
    docker rm law-links-container
    echo "✅ Контейнер остановлен и удален!"
    ;;
  
  restart)
    echo "🔄 Перезапуск контейнера..."
    $0 stop
    $0 start
    ;;
  
  logs)
    echo "📋 Логи контейнера:"
    docker logs law-links-container
    ;;
  
  status)
    echo "📊 Статус контейнера:"
    docker ps | grep law-links-container || echo "Контейнер не запущен"
    ;;
  
  test)
    echo "🧪 Тестирование сервиса..."
    echo ""
    echo "1. Health check:"
    curl -s http://localhost:8978/health | python3 -m json.tool
    echo ""
    echo ""
    echo "2. Простой запрос:"
    curl -X POST "http://localhost:8978/detect" \
      -H "Content-Type: application/json" \
      -d '{"text": "Согласно статье 23 Налогового кодекса РФ"}' \
      -s | python3 -m json.tool
    echo ""
    echo ""
    echo "3. Сложный запрос:"
    curl -X POST "http://localhost:8978/detect" \
      -H "Content-Type: application/json" \
      -d '{"text": "В соответствии с пп. 1 п. 1 ст. 374 НК РФ"}' \
      -s | python3 -m json.tool
    ;;
  
  *)
    echo "Использование: $0 {build|start|stop|restart|logs|status|test}"
    echo ""
    echo "Команды:"
    echo "  build   - Собрать Docker образ"
    echo "  start   - Запустить контейнер"
    echo "  stop    - Остановить и удалить контейнер"
    echo "  restart - Перезапустить контейнер"
    echo "  logs    - Показать логи контейнера"
    echo "  status  - Показать статус контейнера"
    echo "  test    - Протестировать сервис"
    exit 1
    ;;
esac

