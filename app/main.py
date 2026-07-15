import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bm25_store import init_all_indexes
from .config import get_settings
from .db import init_db
from .demo_seed import ensure_demo_account
from .routers import admin, auth, chat, history


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if not s.openrouter_api_key:
        print("[warn] OPENROUTER_API_KEY 未配置，LLM 对话将不可用（页面仍可访问）", flush=True)
    if not s.admin_token:
        print("[warn] ADMIN_TOKEN 未配置，/admin 接口处于无鉴权的开发模式", flush=True)
    init_db()
    ensure_demo_account()
    # BM25 索引放后台线程构建：有磁盘缓存时 <1s，冷缓存 ~10s。
    # 不阻塞启动，/healthz 和静态页立即可用；构建完成前的检索请求
    # 会在线程池里等 get_bm25_index() 的惰性构建，只影响该请求自身。
    async def _warm_bm25():
        try:
            await asyncio.to_thread(init_all_indexes, ["health", "psych"])
        except Exception as e:
            print(f"[warn] BM25 预热失败（检索请求时将按需重试）：{e!r}", flush=True)

    asyncio.create_task(_warm_bm25())
    yield


app = FastAPI(title="ElderCare RAG", version="0.2.0", lifespan=lifespan)
app.include_router(chat.router, tags=["chat"])
app.include_router(auth.router)
app.include_router(history.router)
app.include_router(admin.router)

# 前端静态资源
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/admin")
async def admin_dashboard():
    """仪表盘页面本身不鉴权（空壳 HTML），数据接口 /admin/stats 才校验 token。"""
    return FileResponse(_STATIC_DIR / "admin.html")


@app.get("/healthz")
async def health_check():
    return {"status": "ok"}
