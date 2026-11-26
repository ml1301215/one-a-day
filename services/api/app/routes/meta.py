from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..config import settings
from ..persona.loader import get_persona
from ..safety import router as safety
from ..schemas import ClassifyIn
from ..voice.texture import get_texture

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
        # v4：运行时姿态与语料纹理是否就位，这两个字段决定了回复的形状
        # 由「推理引擎 + 数据分布 + 检索样本」哪一层承担。
        "posture": settings.safety,
        "candidates": settings.candidates,
        "style_gate": settings.style_gate,
        "texture": get_texture(settings.texture_path).available,
        **db.stats(),
    }


@router.post("/safety/classify")
def classify(body: ClassifyIn) -> dict:
    """调试用：看一句话会被判成哪一级。不写库、不生成。

    注意：这个端点在默认姿态（posture=off）下**不参与任何真实请求**，
    它只是把分级器单独暴露出来，方便对照 A/B 和跑金标。
    """
    v = safety.classify(body.text)
    return {
        **v.to_dict(),
        "template": v.template,
        "posture": settings.safety,
    }
