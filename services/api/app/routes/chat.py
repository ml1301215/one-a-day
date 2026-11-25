from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import db, quality, ratelimit
from ..config import settings
from ..llm.providers import get_provider, stream_text
from ..persona.loader import get_persona
from ..prompt import builder
from ..safety import router as safety
from ..schemas import ChatRequest

router = APIRouter(prefix="/api", tags=["chat"])


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _resolve_session(req: ChatRequest) -> dict:
    if req.session_id:
        sess = db.get_session(req.session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="会话不存在")
        return sess
    device = req.device_id or "anonymous"
    return db.create_session(device, req.persona_id or settings.persona_id)


def _should_store_user(req: ChatRequest) -> bool:
    """「更损/更暖」和「展开讲讲」都是对同一句话重生成，不重复记用户消息。

    这里只认请求里的显式标记，不做内容比对：真人完全可能把同一句再发一次，
    那种情况必须算新的一轮，不能用「内容相同」去猜。
    """
    return req.variant == "default" and not req.expand


def _build_messages(
    history: list[dict[str, str]], req: ChatRequest
) -> list[dict[str, str]]:
    """历史 + 本轮用户消息。

    重生成时历史末尾已经是这句话（回复被撤掉了），再塞一遍会变成
    「用户 → 用户」，模型会以为用户连说了两遍。
    """
    messages = list(history)
    tail = messages[-1] if messages else None
    already_there = bool(
        tail and tail.get("role") == "user" and tail.get("content") == req.message
    )
    if not already_there:
        messages.append({"role": "user", "content": req.message})
    return messages


async def _generate(system: str, messages: list[dict[str, str]], humor_scale: float) -> str:
    provider = get_provider()
    raw = await provider.complete(
        system=system,
        messages=messages,
        temperature=settings.temperature,
        top_p=settings.top_p,
        presence_penalty=settings.presence_penalty,
    )
    # 真模型常用空行分段，气泡是 pre-wrap，会渲染成明显空档；统一在这里收口
    return quality.normalize(raw)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    if not ratelimit.allow(f"chat:{req.device_id or 'anon'}"):
        raise HTTPException(status_code=429, detail="太快了，歇一分钟再来")

    sess = _resolve_session(req)
    persona = get_persona(str(settings.persona_path))
    store_user = _should_store_user(req)

    if not store_user:
        # 重生成：先把要被替换的那条回复撤掉，
        # 否则历史末尾会变成「用户 → 旧回复 → 用户」，模型看得见自己上次的说法，容易照着抄。
        db.drop_last_assistant(sess["id"])

    # 历史要在写入当前用户消息之前取，否则会重复
    history = db.recent_history(sess["id"], settings.history_turns)

    # ---- 安全分级 ----
    verdict = safety.classify(req.message, db.recent_levels(sess["id"]))

    if req.dry_run:
        async def dry() -> AsyncIterator[str]:
            yield _sse("meta", {"session_id": sess["id"], **verdict.to_dict()})
            yield _sse("done", {"session_id": sess["id"], "dry_run": True})
        return StreamingResponse(dry(), media_type="text/event-stream")

    if store_user:
        db.add_message(
            sess["id"], "user", req.message, level=verdict.level, persona_id=persona.id
        )

    # ---- 硬切路径：L3 危机模板 + 越界拒绝模板，都不交给模型 ----
    if verdict.template:
        text = verdict.template
        mid = db.add_message(
            sess["id"],
            "assistant",
            text,
            level=verdict.level,
            persona_id=persona.id,
            meta={"template": verdict.template_key, "hard_block": True},
        )
        db.touch_session(sess["id"])

        async def l3_stream() -> AsyncIterator[str]:
            yield _sse(
                "meta",
                {
                    "session_id": sess["id"],
                    **verdict.to_dict(),
                    "template": verdict.template_key,
                },
            )
            async for chunk in stream_text(text, chunk_chars=3, delay=0.01):
                yield _sse("delta", {"text": chunk})
            yield _sse(
                "done",
                {
                    "session_id": sess["id"],
                    "message_id": mid,
                    "level": verdict.level,
                },
            )

        return StreamingResponse(l3_stream(), media_type="text/event-stream")

    # ---- L0/L1/L2：走模型 ----
    extra = builder.variant_directive(req.variant)
    if req.expand:
        extra = (extra + " " + builder.continuation_directive("expand")).strip()

    system = builder.build_system_prompt(
        persona,
        humor_scale=verdict.humor_scale,
        level=verdict.level,
        recent_bits=db.bits_used_recently(sess["id"]),
        extra_directive=extra,
    )
    messages = _build_messages(history, req)

    async def gen() -> AsyncIterator[str]:
        # 先把 meta 送出去：分级、会话 id、幽默系数立刻可见，
        # 用户不会对着一个没有任何反馈的空气泡干等整个生成周期。
        yield _sse("meta", {"session_id": sess["id"], **verdict.to_dict()})

        try:
            draft = await _generate(system, messages, verdict.humor_scale)
        except Exception as exc:  # noqa: BLE001
            # 模型不可用时不能把用户晾着：降级到本地基线。
            # 注意不要把异常信息写进 system——它会污染后续重写请求。
            _ = exc
            fallback = get_provider("mock")
            draft = await fallback.complete(
                system=system,
                messages=messages,
                temperature=settings.temperature,
                top_p=settings.top_p,
                presence_penalty=settings.presence_penalty,
            )

        # ---- 质量闸门：不合格重写一次 ----
        report = quality.check(
            draft, persona, humor_scale=verdict.humor_scale, relaxed=req.expand
        )
        rewrite_used = False
        if not report.ok:
            rewrite_used = True
            try:
                retry_messages = [
                    *messages,
                    {"role": "assistant", "content": draft},
                    {
                        "role": "user",
                        "content": builder.build_rewrite_prompt(
                            persona, draft, report.reason()
                        ),
                    },
                ]
                second = await _generate(system, retry_messages, verdict.humor_scale)
                report2 = quality.check(
                    second, persona, humor_scale=verdict.humor_scale, relaxed=req.expand
                )
                draft = second
                report = report2
            except Exception:  # noqa: BLE001
                pass

        if not report.ok:
            # 最后手段：截到合规形状，宁可少一句也不砸一大段
            draft = quality.truncate_to_shape(draft, persona)

        final_text = draft.strip()

        mid = db.add_message(
            sess["id"],
            "assistant",
            final_text,
            level=verdict.level,
            persona_id=persona.id,
            meta={
                "issues": report.issues,
                "rewrite": rewrite_used,
                "provider": settings.provider,
            },
        )
        db.mark_bits(sess["id"], quality.extract_bits(final_text))
        db.touch_session(sess["id"])

        async for chunk in stream_text(final_text):
            yield _sse("delta", {"text": chunk})
        yield _sse(
            "done",
            {
                "session_id": sess["id"],
                "message_id": mid,
                "level": verdict.level,
                "quality_ok": report.ok,
                "issues": report.issues,
            },
        )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat")
async def chat_once(req: ChatRequest) -> dict:
    """非流式版本，给 CLI 和评测用。"""
    sess = _resolve_session(req)
    persona = get_persona(str(settings.persona_path))
    verdict = safety.classify(req.message, db.recent_levels(sess["id"]))

    if req.dry_run:
        return {
            "session_id": sess["id"],
            "dry_run": True,
            "safety": verdict.to_dict(),
            "template": verdict.template,
        }

    if verdict.template:
        text = verdict.template
    else:
        # 与流式端点同一套重生成语义：换说法要撤掉旧回复，不是叠加
        if not _should_store_user(req):
            db.drop_last_assistant(sess["id"])
        history = db.recent_history(sess["id"], settings.history_turns)
        extra = builder.variant_directive(req.variant)
        if req.expand:
            # 与非流式端点保持同一套拼装，否则两条路径的「展开讲讲」行为不一致
            extra = (extra + " " + builder.continuation_directive("expand")).strip()
        system = builder.build_system_prompt(
            persona,
            humor_scale=verdict.humor_scale,
            level=verdict.level,
            recent_bits=db.bits_used_recently(sess["id"]),
            extra_directive=extra,
        )
        messages = _build_messages(history, req)
        text = await _generate(system, messages, verdict.humor_scale)
        rep = quality.check(text, persona, humor_scale=verdict.humor_scale, relaxed=req.expand)
        if not rep.ok:
            text = quality.truncate_to_shape(text, persona)

    if _should_store_user(req):
        db.add_message(
            sess["id"], "user", req.message, level=verdict.level, persona_id=persona.id
        )
    mid = db.add_message(
        sess["id"], "assistant", text.strip(), level=verdict.level, persona_id=persona.id
    )
    db.mark_bits(sess["id"], quality.extract_bits(text))
    db.touch_session(sess["id"])

    return {
        "session_id": sess["id"],
        "message_id": mid,
        "reply": text.strip(),
        "safety": verdict.to_dict(),
    }
