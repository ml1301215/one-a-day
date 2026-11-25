from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import PERSONA_DIR
from ..persona.loader import available_personas, get_persona, PersonaError

router = APIRouter(prefix="/api", tags=["personas"])


@router.get("/personas")
def list_personas() -> dict:
    return {"personas": available_personas(PERSONA_DIR)}


@router.get("/personas/{persona_id}")
def get_persona_detail(persona_id: str) -> dict:
    path = PERSONA_DIR / persona_id / "persona.yaml"
    try:
        p = get_persona(str(path))
    except PersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # 只暴露可公开的元信息与装置名，不泄露完整 prompt
    return {
        "id": p.id,
        "display_name": p.display_name,
        "version": p.version,
        "license": p.license,
        "worldview": p.raw.get("worldview", {}).get("one_liner", "").strip(),
        "voice": p.voice,
        "devices": [
            {"id": d.id, "name": d.name, "weight": d.weight} for d in p.devices
        ],
        "anti_bits_count": len(p.raw.get("anti_bits", []) or []),
    }
