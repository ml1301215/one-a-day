"""请求/响应模型。字段保持极简——前端只需要这几样。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    device_id: str | None = Field(default=None, max_length=64)
    persona_id: str | None = None

    # 换个说法：默认 / spicier（更损）/ warmer（更暖）
    variant: Literal["default", "spicier", "warmer"] = "default"
    # 展开讲讲
    expand: bool = False
    # 只调试安全分级，不生成回复
    dry_run: bool = False

    # 单次请求临时改写风格闸门。默认跟服务端 OAD_STYLE_GATE。
    # 存在的理由：对练台要能在同一个会话里 A/B 两种闸门，
    # 否则测出来的差异里永远混着「重启了服务」这一个变量。
    gate: Literal["raw", "strict"] | None = None


class SessionCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    persona_id: str | None = None


class SessionOut(BaseModel):
    id: str
    device_id: str
    persona_id: str
    created_at: float


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    level: str | None = None
    created_at: float
    meta: dict = {}


class FeedbackIn(BaseModel):
    message_id: str
    kind: Literal["up", "down"]


class ClassifyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
