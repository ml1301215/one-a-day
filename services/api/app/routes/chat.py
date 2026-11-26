from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import db, quality, ratelimit
from ..config import settings
from ..llm.providers import get_provider, stream_text
from ..persona.loader import get_persona
from ..prompt import builder
from ..schemas import ChatRequest
from ..voice import scorer
from ..voice.texture import get_texture

router = APIRouter(prefix="/api", tags=["chat"])

# 关闭安全分级之后 meta 里仍然给一个中性值：
# 前端已经不再渲染等级标签，但对练台和评测脚本还在读这个字段，
# 直接删掉会让它们从「读到一个 L0」变成 KeyError——这是无谓的破坏。
NEUTRAL_VERDICT = {
    "level": "L0",
    "categories": [],
    "reasons": [],
    "humor_scale": 1.0,
    "hard_block": False,
    "has_template": False,
    "mode": "off",
}


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


def _last_self_story(history: list[dict[str, str]]) -> bool:
    """上一条回复是不是在讲他自己的经历。

    讲自己是这个人最有价值的一拍，但它会自我强化：
    一旦模型上一条讲了「我上回…」，下一条几乎必然也讲。
    连着两轮就变成「每条都有一个小故事」的模板，而且是在抢用户的话题。
    """
    for m in reversed(history or []):
        if m.get("role") == "assistant":
            return quality.self_story(m.get("content") or "")
    return False


def _last_reply_chars(history: list[dict[str, str]]) -> int:
    for m in reversed(history or []):
        if m.get("role") == "assistant":
            return len(re.sub(r"\s", "", m.get("content") or ""))
    return 0


async def _generate_one(system: str, messages: list[dict[str, str]], temperature: float) -> str:
    provider = get_provider()
    raw = await provider.complete(
        system=system,
        messages=messages,
        temperature=temperature,
        top_p=settings.top_p,
        presence_penalty=settings.presence_penalty,
    )
    # 真模型常用空行分段，气泡是 pre-wrap，会渲染成明显空档；统一在这里收口
    return quality.normalize(raw)


async def _generate_best(
    *,
    persona,
    messages: list[dict[str, str]],
    base_kwargs: dict,
    texture,
    n: int,
) -> tuple[str, list[scorer.CandidateScore]]:
    """同轮生成 n 条候选，按语料分布挑一条。

    这是 v4 唯一一处「改完之后行为一定变」的地方，也是 v3.1 文档里
    自己写明「配置项已存在、挑选逻辑未实现」的那个洞。
    没有它，prompt 改十次也压不住「每条都长成同一个形状」。
    """
    n = max(1, n)
    user_text = _last_user_text(messages)

    async def round_with(shots_enabled: bool):
        systems = [
            builder.build_system_prompt(
                **{**base_kwargs, "candidate_index": i, "shots_enabled": shots_enabled}
            )
            for i in range(n)
        ]
        # 温度轻微抖动：三条全同温会生成三条几乎一样的候选，选样就白做了
        temps = [settings.temperature + 0.06 * (i - (n - 1) / 2) for i in range(n)]
        results = await asyncio.gather(
            *[_generate_one(s, messages, t) for s, t in zip(systems, temps)],
            return_exceptions=True,
        )
        drafts = [r for r in results if isinstance(r, str) and r.strip()]
        if not drafts:
            exc = next((r for r in results if isinstance(r, BaseException)), None)
            # 模型不可用不能把用户晾着：降级到本地基线。
            # 注意不要把异常信息写进 system——它会污染后续重写请求。
            _ = exc
            fallback = get_provider("mock")
            text = await fallback.complete(
                system=systems[0],
                messages=messages,
                temperature=settings.temperature,
                top_p=settings.top_p,
                presence_penalty=settings.presence_penalty,
            )
            return quality.normalize(text), []
        return scorer.pick(
            drafts,
            persona=persona,
            texture=texture,
            used_bits=base_kwargs.get("recent_bits"),
            last_self=base_kwargs.get("last_self_story", False),
            user_text=user_text,
            last_reply_chars=base_kwargs.get("last_reply_chars", 0),
        )

    picked, scored = await round_with(True)

    # ---- 重试通道：三条都在抄样本 ----
    # 实测出现过这种情况：越对题的样本越容易被逐字搬用（「辞职」「大家庭」最明显）。
    # 那时候不是候选的问题，是样本的问题——最好的样本会把模型变成复读机。
    # 所以再要一轮「不给样本」的版本，让模型自己说。只重试一次，不递归。
    copied_all = bool(scored) and not any(s.ok for s in scored) and any(
        any("照抄" in f for f in s.fatal) for s in scored
    )
    if copied_all:
        return await round_with(False)

    return picked, scored



def _last_user_text(messages: list[dict[str, str]]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def _apply_no_self(report: quality.QualityReport, text: str, no_self: bool):
    """「这一条又讲了你自己」——只在连着两条时收紧。

    单条讲自己是他最核心的一拍，不能碰。
    连着两条就是抢戏：用户说一句，他接一句自己的往事，五轮下来
    这场对话里没有用户，只有他的素材。实测（v3.1 多轮）自述序列是
    否/是/是/是/否，撤样本压不住，模型会自己编一段。
    """
    if no_self and quality.self_story(text):
        report.issues.append("又讲了一遍你自己（上一句已经在讲了）")
        report.ok = False
    return report


async def _polish(
    draft: str,
    *,
    persona,
    verdict,
    relaxed: bool,
    system: str,
    messages: list[dict[str, str]],
    strict: bool | None = None,
    no_self: bool = False,
    texture=None,
) -> tuple[str, quality.QualityReport, bool]:
    """质量闸门：不合格就重写一次，仍不合格就压形状。

    v4 把默认闸门从 raw 收窄到 format：只拦 emoji / Markdown / 分点列表 /
    空回复 / 助手腔 / 照抄示例。风格与长度不再触发重写——
    重写会把一句「有点糙但像人」的话，改成一句「通顺但不像人」的话，
    这个代价 v3 已经用一整版验证过了，改由候选重排承担。
    """
    if strict is None:
        strict = settings.style_gate == "strict"

    # 用户那条消息有多长。他讲了一整件事（有时间、有人、有后果），
    # 就不能回一句「你回去住哪儿」了事——那不是懒，是敷衍，两者不是一回事。
    user_len = len(re.sub(r"\s", "", _last_user_text(messages)))
    min_chars = 12 if user_len >= 10 else 0
    humor_scale = getattr(verdict, "humor_scale", 1.0) if verdict is not None else 1.0
    report = quality.check(
        draft,
        persona,
        humor_scale=humor_scale,
        relaxed=relaxed,
        strict=strict,
        min_chars=min_chars,
    )
    report = _apply_no_self(report, draft, no_self)
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
                        persona, draft, report.reason(), no_self=no_self, texture=texture
                    ),
                },
            ]
            second = await _generate_one(system, retry_messages, settings.temperature)
            draft, report = second, quality.check(
                second,
                persona,
                humor_scale=humor_scale,
                relaxed=relaxed,
                strict=strict,
                min_chars=min_chars,
            )
            report = _apply_no_self(report, draft, no_self)
        except Exception:  # noqa: BLE001
            pass

    if not report.ok:
        # 最后手段：截到合规形状，宁可少一句也不砸一大段
        draft = quality.truncate_to_shape(draft, persona)

    return draft.strip(), report, rewrite_used


def _classify(req: ChatRequest, sess: dict, persona):
    """只在 guard 姿态下分类；默认姿态返回 None，主链路上没有任何安全分支。"""
    if not settings.guard_enabled:
        return None
    from ..safety import router as safety  # 延迟导入：off 姿态下这个模块不参与启动

    return safety.classify(req.message, db.recent_levels(sess["id"]))


def _meta_payload(sess_id: str, verdict, extra: dict | None = None) -> dict:
    payload = {"session_id": sess_id, **NEUTRAL_VERDICT}
    if verdict is not None:
        payload.update(verdict.to_dict())
    payload.update(extra or {})
    return payload


def _candidate_count() -> int:
    # mock 是确定性基线，多生成几条完全一样，白烧 CPU
    if settings.provider == "mock":
        return 1
    return max(1, settings.candidates)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    if not ratelimit.allow(f"chat:{req.device_id or 'anon'}"):
        raise HTTPException(status_code=429, detail="太快了，歇一分钟再来")

    sess = _resolve_session(req)
    persona = get_persona(str(settings.persona_path))
    texture = get_texture(settings.texture_path)
    store_user = _should_store_user(req)

    if not store_user:
        # 重生成：先把要被替换的那条回复撤掉，
        # 否则历史末尾会变成「用户 → 旧回复 → 用户」，模型看得见自己上次的说法，容易照着抄。
        db.drop_last_assistant(sess["id"])

    # 历史要在写入当前用户消息之前取，否则会重复
    history = db.recent_history(sess["id"], settings.history_turns)
    verdict = _classify(req, sess, persona)
    n = _candidate_count()

    if req.dry_run:
        async def dry() -> AsyncIterator[str]:
            yield _sse("meta", _meta_payload(sess["id"], verdict, {"candidates": n}))
            yield _sse("done", {"session_id": sess["id"], "dry_run": True})
        return StreamingResponse(dry(), media_type="text/event-stream")

    if store_user:
        db.add_message(
            sess["id"],
            "user",
            req.message,
            level=verdict.level if verdict else None,
            persona_id=persona.id,
        )

    # ---- guard 姿态下的硬切路径：模板不交给模型 ----
    if verdict is not None and verdict.template:
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
                _meta_payload(sess["id"], verdict, {"template": verdict.template_key}),
            )
            async for chunk in stream_text(text, chunk_chars=3, delay=0.01):
                yield _sse("delta", {"text": chunk})
            yield _sse(
                "done",
                {"session_id": sess["id"], "message_id": mid, "level": verdict.level},
            )

        return StreamingResponse(l3_stream(), media_type="text/event-stream")

    # ---- 正常路径 ----
    extra = builder.variant_directive(req.variant)
    if req.expand:
        extra = (extra + " " + builder.continuation_directive("expand")).strip()

    last_self = _last_self_story(history)
    build_kwargs = dict(
        persona=persona,
        humor_scale=verdict.humor_scale if verdict else 1.0,
        level=verdict.level if verdict else "L0",
        recent_bits=db.bits_used_recently(sess["id"]),
        extra_directive=extra,
        last_self_story=last_self,
        user_text=req.message,
        texture=texture,
        last_reply_chars=_last_reply_chars(history),
    )
    messages = _build_messages(history, req)

    async def gen() -> AsyncIterator[str]:
        # 先把 meta 送出去：会话 id 与候选数量立刻可见，
        # 用户不会对着一个没有任何反馈的空气泡干等整个生成周期。
        yield _sse("meta", _meta_payload(sess["id"], verdict, {"candidates": n}))

        draft, scored = await _generate_best(
            persona=persona,
            messages=messages,
            base_kwargs=build_kwargs,
            texture=texture,
            n=n,
        )

        # ---- 质量闸门：只拦格式类问题 ----
        draft, report, rewrite_used = await _polish(
            draft,
            persona=persona,
            verdict=verdict,
            relaxed=req.expand,
            system=builder.build_system_prompt(**build_kwargs),
            messages=messages,
            strict=(req.gate or settings.style_gate) == "strict",
            no_self=last_self,
            texture=texture,
        )

        final_text = draft.strip()

        mid = db.add_message(
            sess["id"],
            "assistant",
            final_text,
            level=verdict.level if verdict else None,
            persona_id=persona.id,
            meta={
                "issues": report.issues,
                "rewrite": rewrite_used,
                "provider": settings.provider,
                "candidates": [s.summary() for s in scored],
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
                "level": verdict.level if verdict else None,
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
    """非流式版本，给 CLI、对练台和评测用。"""
    sess = _resolve_session(req)
    persona = get_persona(str(settings.persona_path))
    texture = get_texture(settings.texture_path)
    verdict = _classify(req, sess, persona)
    n = _candidate_count()

    if req.dry_run:
        return {
            "session_id": sess["id"],
            "dry_run": True,
            "safety": _meta_payload(sess["id"], verdict),
            "template": verdict.template if verdict else None,
        }

    text = ""
    rep = None
    rewrite_used = False
    scored: list[scorer.CandidateScore] = []

    if verdict is not None and verdict.template:
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
        last_self = _last_self_story(history)
        build_kwargs = dict(
            persona=persona,
            humor_scale=verdict.humor_scale if verdict else 1.0,
            level=verdict.level if verdict else "L0",
            recent_bits=db.bits_used_recently(sess["id"]),
            extra_directive=extra,
            last_self_story=last_self,
            user_text=req.message,
            texture=texture,
            last_reply_chars=_last_reply_chars(history),
        )
        messages = _build_messages(history, req)
        draft, scored = await _generate_best(
            persona=persona,
            messages=messages,
            base_kwargs=build_kwargs,
            texture=texture,
            n=n,
        )
        text, rep, rewrite_used = await _polish(
            draft,
            persona=persona,
            verdict=verdict,
            relaxed=req.expand,
            system=builder.build_system_prompt(**build_kwargs),
            messages=messages,
            strict=(req.gate or settings.style_gate) == "strict",
            no_self=last_self,
            texture=texture,
        )

    if _should_store_user(req):
        db.add_message(
            sess["id"],
            "user",
            req.message,
            level=verdict.level if verdict else None,
            persona_id=persona.id,
        )
    mid = db.add_message(
        sess["id"],
        "assistant",
        text.strip(),
        level=verdict.level if verdict else None,
        persona_id=persona.id,
        meta={
            "issues": rep.issues if rep else [],
            "rewrite": rewrite_used,
            "provider": settings.provider,
            "candidates": [s.summary() for s in scored],
        },
    )
    db.mark_bits(sess["id"], quality.extract_bits(text))
    db.touch_session(sess["id"])

    body = text.strip()
    return {
        "session_id": sess["id"],
        "message_id": mid,
        "reply": body,
        "safety": _meta_payload(sess["id"], verdict),
        "persona": {"id": persona.id, "version": persona.version},
        # 对练台要看的体检单。以前这些只写进 db.meta，前端和测试脚本都拿不到，
        # 于是「这句话为什么不合格」只能去翻 sqlite。
        "quality": {
            "ok": rep.ok if rep else True,
            "issues": rep.issues if rep else [],
            "sentences": rep.sentences if rep else quality.count_sentences(body),
            "chars": rep.chars if rep else len(re.sub(r"\s", "", body)),
            "self_story": quality.self_story(body),
            "copied": quality.copied_from_shots(body, persona) or "",
            "rewrite": rewrite_used,
            "gate": req.gate or settings.style_gate,
            "candidates": [s.summary() for s in scored],
            "texture": texture.available,
        },
    }
