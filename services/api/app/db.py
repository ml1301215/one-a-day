"""SQLite 存储。不引 ORM——这个项目的表结构三张就够，ORM 只会增加贡献门槛。"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL,
    persona_id  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_device ON sessions(device_id);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    level       TEXT,
    persona_id  TEXT,
    meta        TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS used_bits (
    session_id  TEXT NOT NULL,
    bit         TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (session_id, bit, created_at)
);

CREATE TABLE IF NOT EXISTS feedback (
    message_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_msg ON feedback(message_id);
"""


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    _ensure_parent(settings.db_path)
    conn = sqlite3.connect(settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
def create_session(device_id: str, persona_id: str) -> dict[str, Any]:
    sid = uuid.uuid4().hex
    ts = now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, device_id, persona_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (sid, device_id, persona_id, ts, ts),
        )
    return {"id": sid, "device_id": device_id, "persona_id": persona_id, "created_at": ts}


def get_session(session_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def touch_session(session_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), session_id))


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------
def add_message(
    session_id: str,
    role: str,
    content: str,
    level: str | None = None,
    persona_id: str | None = None,
    meta: dict | None = None,
) -> str:
    mid = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, level, persona_id, meta, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                mid,
                session_id,
                role,
                content,
                level,
                persona_id,
                json.dumps(meta or {}, ensure_ascii=False),
                now(),
            ),
        )
    return mid


def list_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except json.JSONDecodeError:
            d["meta"] = {}
        out.append(d)
    return out


def recent_levels(session_id: str, n: int = 5) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT level FROM messages WHERE session_id=? AND role='user' AND level IS NOT NULL"
            " ORDER BY created_at DESC LIMIT ?",
            (session_id, n),
        ).fetchall()
    return [r["level"] for r in reversed(rows)]


def recent_history(session_id: str, turns: int) -> list[dict[str, str]]:
    """取最近若干轮，拼成模型消息格式。

    注意不能复用 list_messages(limit=N)——那个是 ASC LIMIT，取的是最早的 N 条。
    长会话里会把开场白当成上下文，症状是模型突然答非所问。
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages"
            " WHERE session_id=? AND role IN ('user','assistant')"
            " ORDER BY created_at DESC LIMIT ?",
            (session_id, max(1, turns) * 2),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------------------------------------------------------------------------
# 梗表：同一梗 24h 内不复用
# ---------------------------------------------------------------------------
BIT_TTL_SECONDS = 24 * 3600


def bits_used_recently(session_id: str) -> list[str]:
    cutoff = now() - BIT_TTL_SECONDS
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT bit FROM used_bits WHERE session_id=? AND created_at>?",
            (session_id, cutoff),
        ).fetchall()
    return [r["bit"] for r in rows]


def mark_bits(session_id: str, bits: list[str]) -> None:
    if not bits:
        return
    ts = now()
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO used_bits (session_id, bit, created_at) VALUES (?,?,?)",
            [(session_id, b, ts) for b in bits],
        )


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------
def add_feedback(message_id: str, kind: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO feedback (message_id, kind, created_at) VALUES (?,?,?)",
            (message_id, kind, now()),
        )


def stats() -> dict[str, int]:
    with connect() as conn:
        s = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
        m = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    return {"sessions": s, "messages": m}
