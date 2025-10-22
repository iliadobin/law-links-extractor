from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

@app.get("/sum")
def s(a: int, b: int):
    return {"sum": a + b}