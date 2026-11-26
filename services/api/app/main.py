from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from . import db
from .config import settings
from .routes import chat, meta, personas, sessions

DESCRIPTION = """
一日一片 · OneADay —— 人格驱动的聊天后端。

- 人格包与引擎解耦，默认内置「布洛芬体」v4
- 生成链路：检索样本 → 多候选生成 → 按语料分布重排（app/voice/）
- 语料纹理（texture.json）来自 tools/distill 对 19553 条弹幕 + 10972 条评论的统计
- 安全分级默认关闭（OAD_SAFETY=off）；需要合规部署时可显式开成 guard
- 默认 mock provider，零 API key 也能跑
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


# 建表是幂等的（IF NOT EXISTS），放在模块级是为了让 TestClient、
# 脚本直接 import、以及没跑 lifespan 的场景都不会拿到空库。
db.init_db()


app = FastAPI(
    title="OneADay API",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(personas.router)


@app.get("/")
def root() -> dict:
    return {
        "name": "OneADay API",
        "version": "0.1.0",
        "docs": "/docs",
        "provider": settings.provider,
        "bench": "/demo",
    }


BENCH = Path(__file__).parent / "static" / "bench.html"


@app.get("/demo", include_in_schema=False)
def bench():
    """对练台。

    由后端自己发同一个 origin，省掉跨域配置——对练台要用来看「这句话为什么
    被判不合格」，而不是来调 CORS 的。文件不在也不该 500，给一句人话。
    """
    if not BENCH.is_file():
        return HTMLResponse("<p style='font:15px sans-serif;padding:24px'>"
                            "找不到 static/bench.html。</p>", status_code=404)
    return FileResponse(BENCH, media_type="text/html; charset=utf-8")
