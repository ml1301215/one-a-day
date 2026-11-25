"""回复质量闸门。

在文本流给用户之前跑一遍。不合格就重写一次，仍不合格则降级处理。
这里只做「客观可判」的检查——风格好不好需要人评，但超长、带 emoji、
带 Markdown、说违禁词，这些机器能判，就不该放过去。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .persona.loader import Persona

EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U0001F1E6-\U0001F1FF" "\u2600-\u27BF" "\uFE0F" "\u2B00-\u2BFF" "]"
)
MARKDOWN_RE = re.compile(r"(\*\*|^#{1,6}\s|```|^\s*[-*+]\s|^\s*\d+\.\s)", re.MULTILINE)
LIST_RE = re.compile(r"^\s*(\d+[.、)]|[-*+•])\s", re.MULTILINE)
AI_TELL_RE = re.compile(r"(作为一个?\s*AI|作为语言模型|我理解你的感受|希望(对你|能)有(所)?帮助)")
SENT_SPLIT = re.compile(r"[。！？!?…\n]+")


@dataclass
class QualityReport:
    ok: bool
    issues: list[str] = field(default_factory=list)
    sentences: int = 0
    chars: int = 0
    text: str = ""

    def reason(self) -> str:
        return "；".join(self.issues) if self.issues else ""


def count_sentences(text: str) -> int:
    return len([s for s in SENT_SPLIT.split(text) if s.strip()])


def check(
    text: str,
    persona: Persona,
    humor_scale: float = 1.0,
    relaxed: bool = False,
) -> QualityReport:
    issues: list[str] = []
    t = (text or "").strip()

    if not t:
        return QualityReport(ok=False, issues=["空回复"], text=t)

    # 长度：安全等级低或用户要求展开时放宽
    sent_limit = persona.sentence_max + (3 if relaxed else 0)
    char_limit = int(persona.char_max * (2.2 if relaxed else 1.0))

    n_sent = count_sentences(t)
    n_char = len(re.sub(r"\s", "", t))

    if n_sent > sent_limit:
        issues.append(f"句数 {n_sent} 超过上限 {sent_limit}")
    if n_char > char_limit:
        issues.append(f"字数 {n_char} 超过上限 {char_limit}")

    if EMOJI_RE.search(t):
        issues.append("出现 emoji")

    if MARKDOWN_RE.search(t):
        issues.append("出现 Markdown 标记")

    if LIST_RE.search(t):
        issues.append("出现分点列表")

    if AI_TELL_RE.search(t):
        issues.append("出现 AI 腔套话")

    lowered = t.lower()
    for bit in persona.raw.get("anti_bits", []) or []:
        if bit and bit.lower() in lowered:
            issues.append(f"命中禁用表达「{bit}」")

    # 危机模板不做长度约束（L3 需要把话说清楚）
    if humor_scale <= 0.0 and n_char > 0:
        issues = [i for i in issues if not i.startswith("字数") and not i.startswith("句数")]

    return QualityReport(
        ok=not issues,
        issues=issues,
        sentences=n_sent,
        chars=n_char,
        text=t,
    )


def truncate_to_shape(text: str, persona: Persona) -> str:
    """最后手段：超长时压到合规形状。

    关键：不能简单截前 N 句。回复的价值在首尾——开头负责「被接住」，
    结尾负责「明天能做点什么」。中间的解释才是可牺牲的。
    这与 persona 的 merge_rule（超长先砍 translate）一致。
    """
    parts = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    limit = persona.sentence_max
    if not parts:
        return text.strip()
    if len(parts) <= limit:
        keep = parts
    elif limit <= 2:
        keep = parts[:limit]
    else:
        # 首句 + 末尾若干句，中间的解释性内容让位
        keep = [parts[0], *parts[-(limit - 1) :]]

    out = "。".join(keep)
    if out and out[-1] not in "。！？…":
        out += "。"
    return out


def extract_bits(text: str) -> list[str]:
    """提取「梗指纹」：每句开头几个字。

    目的不是精确识别梗，而是让模型别连续用同一种起手式——
    重复的句式比重复的梗更明显地暴露「这是个模板」。
    """
    bits: list[str] = []
    for s in SENT_SPLIT.split(text):
        s = s.strip()
        if len(s) >= 6:
            bits.append(s[:6])
    return bits[:6]
