from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..config import settings
from ..persona.loader import get_persona
from ..safety import router as safety
from ..schemas import ClassifyIn

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health() -> dict:
    try:
        version = get_persona(str(settings.persona_path)).version
    except Exception:  # noqa: BLE001 - 健康检查不该因为人格包坏了而整体 500
        version = None
    return {
        "ok": True,
        "provider": settings.provider,
        "persona": settings.persona_id,
        "persona_version": version,
        "storage": "sqlite",
        **db.stats(),
    }


@router.post("/safety/classify")
def classify(body: ClassifyIn) -> dict:
    """调试用：看一句话会被判成哪一级。不写库、不生成。"""
    v = safety.classify(body.text)
    return {
        **v.to_dict(),
        "template": v.template,
    }
