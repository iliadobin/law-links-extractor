import json
from typing import List, Optional, Dict

import uvicorn
from fastapi import FastAPI, Request, Depends
from pydantic import BaseModel
from contextlib import asynccontextmanager


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
    with open("law_aliases.json", "r") as file:
        codex_aliases = json.load(file)
    
    app.state.codex_aliases = codex_aliases
    print("🚀 Сервис запускается...")
    yield
    # Shutdown
    del codex_aliases
    print("🛑 Сервис завершается...")


def get_codex_aliases(request: Request) -> Dict:
    return request.app.state.codex_aliases


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
    codex_aliases: Dict = Depends(get_codex_aliases),
    ) -> LinksResponse:
    """
    Принимает текст и возвращает список юридических ссылок
    """
    # Место для алгоритма распознавания ссылок
    text_length = len(data.text)
    print(codex_aliases["1"])

    sample_links = [
        LawLink(
            law_id=1,
            article="12",
            point_article="б",
        ),
        LawLink(
            law_id=2,
            article="45",
            point_article="3",
            subpoint_article="б",
        )
    ]
    return LinksResponse(links=sample_links)


@app.get("/health")
async def health_check():
    """
    Проверка состояния сервиса
    """
    return {"status": "healthy"}



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8978)