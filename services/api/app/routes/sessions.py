from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..config import settings
from ..schemas import FeedbackIn, SessionCreate

router = APIRouter(prefix="/api", tags=["sessions"])


@router.post("/sessions")
def create_session(body: SessionCreate) -> dict:
    sess = db.create_session(body.device_id, body.persona_id or settings.persona_id)
    return sess


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    return sess


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str, limit: int = 200) -> dict:
    if not db.get_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"messages": db.list_messages(session_id, limit=min(limit, 500))}


@router.post("/feedback")
def feedback(body: FeedbackIn) -> dict:
    db.add_feedback(body.message_id, body.kind)
    return {"ok": True}
