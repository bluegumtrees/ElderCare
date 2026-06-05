from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bm25_store import init_all_indexes
from .db import init_db
from .routers import admin, chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 启动时构建 BM25 索引（约 5-15 秒，15k 文档）
    init_all_indexes(["health", "psych"])
    yield


app = FastAPI(title="ElderCare RAG", version="0.1.0", lifespan=lifespan)
app.include_router(chat.router, tags=["chat"])
app.include_router(admin.router)

# 前端静态资源
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/healthz")
async def health_check():
    return {"status": "ok"}
