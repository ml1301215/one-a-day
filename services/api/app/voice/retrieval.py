"""样本检索：按「用户这句话在说哪件事」挑人格示例，而不是每轮都给同样三条。

v3 的 `_select_few_shots()` 是按长度挑的：最长的一条 + 最短的一条 + 同等级一条。
它解决了一个真问题（三条不能同构），但引入了另一个：**同一个话题永远拿到同一批示例**。
用户问「同事抢功」和问「我妈催婚」，模型看到的样本是同样的三条，
于是它学到的不是「怎么回答这件事」，是「这三句的语感」。

v4 把示例从 13 条扩到 ~55 条（`shotbank.jsonl`），并在这里按话题 + 长度 + 形态检索。
样本的权重高于规则（docs 6.9.6 验证过三次），所以让样本「对题」比让规则更细更有效。

检索刻意不用向量、不用模型：一个 55 条的库，字符 bigram + 话题标签已经够用，
而引入 embedding 会让「为什么挑了这一条」变得没法解释——这个项目的每条决策都要求可反驳。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import quality
from ..persona.loader import Persona
from . import topics


@dataclass
class Shot:
    id: str
    user: str
    reply: str
    topic: str = topics.DEFAULT_TOPIC
    form: str = "两句"
    origin: str = "persona"
    tags: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len([c for c in self.reply if c.strip()])

    @property
    def sentences(self) -> int:
        return quality.count_sentences(self.reply)

    @property
    def has_self(self) -> bool:
        return quality.self_story(self.reply)

    @property
    def opener(self) -> str:
        first = self.reply.split("\n")[0].strip()
        return first[:4]


def _bigrams(text: str) -> set[str]:
    t = "".join(c for c in text or "" if "\u4e00" <= c <= "\u9fff")
    return {t[i : i + 2] for i in range(len(t) - 1)} or {t} if t else set()


def _overlap(a: str, b: str) -> float:
    """字符 bigram 的 Jaccard。中文没有词边界，bigram 是这里最便宜的重合度。"""
    sa, sb = _bigrams(a), _bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _infer_form(reply: str) -> str:
    n = quality.count_sentences(reply)
    if quality.self_story(reply):
        return "自述"
    return {1: "一句", 2: "两句"}.get(n, "多句")


def _shots_from_persona(persona: Persona) -> list[Shot]:
    out: list[Shot] = []
    for i, s in enumerate(persona.raw.get("few_shots") or []):
        reply = str(s.get("reply") or "").strip()
        user = str(s.get("user") or "").strip()
        if not reply or not user:
            continue
        out.append(
            Shot(
                id=f"persona-{i}",
                user=user,
                reply=reply,
                topic=str(s.get("topic") or topics.detect(user)),
                form=str(s.get("form") or _infer_form(reply)),
                origin="persona",
                tags=[str(t) for t in (s.get("tags") or [])],
            )
        )
    return out


def _shots_from_bank(path: Path) -> list[Shot]:
    if not path.is_file():
        return []
    out: list[Shot] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            reply = str(d.get("reply") or "").strip()
            user = str(d.get("user") or "").strip()
            if not reply or not user:
                continue
            out.append(
                Shot(
                    id=str(d.get("id") or f"bank-{len(out)}"),
                    user=user,
                    reply=reply,
                    topic=str(d.get("topic") or topics.detect(user)),
                    form=str(d.get("form") or _infer_form(reply)),
                    origin=str(d.get("origin") or "shotbank"),
                    tags=[str(t) for t in (d.get("tags") or [])],
                )
            )
    except (OSError, ValueError):
        return []
    return out


def load_shots(persona: Persona) -> list[Shot]:
    """人格包里的 few_shots + 语料蒸馏出来的 shotbank，同一条回复只留一份。

    先去重再排序：shotbank 里可能有和 few_shots 撞车的句子，
    两份同时进 prompt 会让模型以为那是重点。
    """
    shots = _shots_from_persona(persona)
    bank = _shots_from_bank(persona.path.parent / "shotbank.jsonl")
    seen = {"".join(s.reply.split()) for s in shots}
    for s in bank:
        key = "".join(s.reply.split())
        if key in seen:
            continue
        seen.add(key)
        shots.append(s)
    return shots


def sample_lines(persona: Persona) -> list[str]:
    """所有样本的每一行，给「近似照抄」检测用。

    用 mtime 做缓存键：shotbank.jsonl 是离线生成的产物，改一次文件就失效一次，
    不需要重启服务。人格包的 few_shots 直接在内存里，不用读盘。
    """
    bank = persona.path.parent / "shotbank.jsonl"
    key = (str(persona.path), bank.stat().st_mtime if bank.is_file() else 0.0)
    if _LINES_CACHE.get("key") != key:
        lines: list[str] = []
        for s in (persona.raw.get("few_shots") or []):
            lines.extend(str(s.get("reply", "")).split("\n"))
        for s in _shots_from_bank(bank):
            lines.extend(s.reply.split("\n"))
        _LINES_CACHE["key"] = key
        _LINES_CACHE["lines"] = [ln for ln in lines if ln.strip()]
    return _LINES_CACHE["lines"]


_LINES_CACHE: dict = {"key": None, "lines": []}


def retrieve(
    persona: Persona,
    user_text: str,
    *,
    k: int = 4,
    avoid_self: bool = False,
    used_bits: list[str] | None = None,
    last_reply_chars: int = 0,
) -> list[Shot]:
    """挑 k 条样本。排序依据只有三件事：对题、形态不撞、长度参差。

    `avoid_self` 为真时整批撤掉「讲自己」的样本——这是 v3 验证过的唯一有效杠杆：
    规则写「这条别讲自己」压不住样本，撤样本可以。
    """
    shots = load_shots(persona)
    if not shots:
        return []

    want_topic = topics.detect(user_text)
    bits = {b[:4] for b in (used_bits or []) if b}

    scored: list[tuple[float, Shot]] = []
    for s in shots:
        score = 0.0
        if s.topic == want_topic:
            score += 3.0
        score += 4.0 * _overlap(user_text, s.user)
        if set(s.tags) & {want_topic}:
            score += 0.5
        # 起手式用过的降权：重复的开头比重复的梗更容易被认出来
        if s.opener and s.opener in bits:
            score -= 1.5
        # 和上一条回复长度几乎一样，说明又要写成同一个形状
        if last_reply_chars and abs(s.chars - last_reply_chars) <= 4:
            score -= 0.6
        scored.append((score, s))

    scored.sort(key=lambda x: -x[0])

    picked: list[Shot] = []
    counts: dict[str, int] = {}
    for _, s in scored:
        if len(picked) >= k:
            break
        if avoid_self and s.has_self:
            continue
        # 形态最多重复两次：三条全是一句、或三条全在讲自己，都会变成新模板
        if counts.get(s.form, 0) >= 2:
            continue
        picked.append(s)
        counts[s.form] = counts.get(s.form, 0) + 1

    if len(picked) < min(k, len(scored)):
        picked = _fill_by_form(scored, picked, k, avoid_self)

    picked = _fix_length_spread(picked, scored, k, avoid_self)
    return picked[:k]


def _fill_by_form(
    scored: list[tuple[float, Shot]], picked: list[Shot], k: int, avoid_self: bool
) -> list[Shot]:
    counts: dict[str, int] = {}
    for s in picked:
        counts[s.form] = counts.get(s.form, 0) + 1
    for _, s in scored:
        if len(picked) >= k:
            break
        if s in picked or (avoid_self and s.has_self):
            continue
        if counts.get(s.form, 0) >= 2:
            continue
        picked.append(s)
        counts[s.form] = counts.get(s.form, 0) + 1
    return picked


def _fix_length_spread(
    picked: list[Shot], scored: list[tuple[float, Shot]], k: int, avoid_self: bool
) -> list[Shot]:
    """三条一样长是最容易被识破的模板，比「每条都给建议」更隐蔽。

    如果选出来的样本长度极差不到 12 字，换掉中间那条，换一条长度离得远的。
    """
    if len(picked) < 2:
        return picked
    lens = [s.chars for s in picked]
    if max(lens) - min(lens) >= 12:
        return picked
    for _, cand in scored:
        if cand in picked or (avoid_self and cand.has_self):
            continue
        if all(abs(cand.chars - x) >= 10 for x in lens):
            picked[-1] = cand
            break
    return picked


def digest(picked: list[Shot]) -> str:
    """检索结果 → prompt 里的一段。强调「长度参差」与「多数不给建议」。"""
    if not picked:
        return ""
    blocks = []
    for s in picked:
        blocks.append(f"用户：{s.user}\n你：\n{s.reply}")
    span = f"{min(s.chars for s in picked)}–{max(s.chars for s in picked)} 字"
    return (
        "【语感参照，不是可复用的台词】\n"
        f"这 {len(picked)} 条来自不同话题、长度从 {span} 不等——这个参差本身就是重点。\n"
        "注意它们大多数**没有给建议**，也没有解释自己刚才为什么这么说。\n"
        "情节不许照搬，换成用户这件事里真实存在的细节；起手式也不许照搬。\n\n"
        + "\n\n".join(blocks)
    )
