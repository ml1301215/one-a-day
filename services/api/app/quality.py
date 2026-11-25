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
AI_TELL_RE = re.compile(
    r"(作为一个?\s*AI|作为语言模型|我理解你的感受|希望(对你|能)有(所)?帮助"
    r"|我明白你的|你的感受是真实的|这是人之常情|这很正常"
    r"|从另一个角度|更重要的是|也就是说|总的来说|不知道你有没有"
    r"|如果你愿意(的话)?[，,]?(可以|不妨))"
)
# 分句起头的填充词。这是「模型在组织语言」的痕迹，但只在这个位置违规：
# 「她其实怕的是」是正常语义，「其实，我觉得」才是废话。
LEADING_FILLER_RE = re.compile(r"(?:^|[，。！？；、\n])\s*(其实|说白了|总而言之|总之|说到底|换句话说)")
# 对称排比：这是「像作文」而不是「像消息」的典型标志。
# 单独一次是修辞，连着两次就是模板。
PARALLEL_RE = re.compile(r"(不是|并非|不只是)[^。！？\n]{0,14}(而是|是)")
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


MIN_SENT_CHARS = 5


def count_sentences(text: str) -> int:
    """句数。

    短于 5 字的片段不计入：这个人的标志之一就是把一句话硬切成两截
    （「当众说。我去年也挨过一次。」），前半截是断句不是新句子。
    按标点硬数会让这种正常断句被判超长，逼模型写回长句、反而更像模型。
    """
    return len([s for s in SENT_SPLIT.split(text) if len(s.strip()) >= MIN_SENT_CHARS])


_MULTI_NL = re.compile(r"[ \t]*\n[ \t]*(?:\n[ \t]*)+")


def normalize(text: str) -> str:
    """统一模型输出的空白：把段落间空行压成单换行。

    真模型（DeepSeek 等）习惯用空行分段，而气泡是 pre-wrap 渲染，
    空行会变成两条明显的缝。压成单换行后，视觉上仍是「一句一行」，
    和 mock 的输出形态一致。
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = _MULTI_NL.sub("\n", t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    return t.strip()


def _norm_for_copy(text: str) -> str:
    return re.sub(r"[\s。，、！？…“”「」『』（）()：:；;]", "", text or "")


def copied_from_shots(text: str, persona: Persona, min_len: int = 10) -> str | None:
    """检查是否原样抄了人格包里的示例句。

    这是个真实发生过的问题：给了 few_shots 之后，模型对「抢功劳」这类场景
    会直接输出示例的第一句（「改名字这一步还挺费事的」），
    用户每问一次得到同一句话。示例是用来传递节奏的，不是台词库。

    只比对长度 ≥ min_len 的整句，避免把常用短句误判成抄袭。
    """
    t = _norm_for_copy(text)
    for shot in persona.raw.get("few_shots") or []:
        for line in str(shot.get("reply", "")).split("\n"):
            n = _norm_for_copy(line)
            if len(n) >= min_len and n in t:
                return line.strip()
    return None


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

    # 「不是A而是B」连用两次以上 = 对称排比，读起来像作文
    if len(PARALLEL_RE.findall(t)) >= 2:
        issues.append("出现对称排比（像作文，不像消息）")

    filler = LEADING_FILLER_RE.search(t)
    if filler:
        issues.append(f"分句起头用填充词「{filler.group(1)}」")

    # 安全模板（humor_scale=0）本来就是标准话术，与示例重合是设计使然，不算抄
    copied = copied_from_shots(t, persona)
    if copied and humor_scale > 0:
        issues.append(f"照抄了示例句「{copied}」")

    # 通篇没有一句短句，说明是在写段落而不是发消息。
    # 只作为 issue，不阻断：有时确实需要把一件事讲完。
    pieces = [s.strip() for s in SENT_SPLIT.split(t) if s.strip()]
    if len(pieces) >= 3 and min(len(s) for s in pieces) > 16:
        issues.append("每句都很长，没有短句（像段落，不像消息）")

    lowered = t.lower()
    for bit in persona.raw.get("anti_bits", []) or []:
        if bit and bit.lower() in lowered:
            issues.append(f"命中禁用表达「{bit}」")

    # 反 AI 味的句子级黑名单（与 anti_bits 分开：这些是「太像助手」而非「太像鸡汤」）
    for phrase in ((persona.raw.get("anti_ai_flavor", {}) or {}).get("phrasings") or []):
        p = str(phrase).strip()
        # 只对 4 字以上的短语做子串匹配，避免「我懂」这类短词误伤
        if len(p) >= 4 and p.lower() in lowered:
            issues.append(f"助手腔「{p}」")

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

    两个约束：

    1. 不能简单截前 N 句。回复的价值在首尾——开头负责「被接住」，
       结尾负责落点。所以超长时保留第一句 + 最后几句，砍中间的解释。

    2. 用换行拼接，不要用句号拼成一整段。
       气泡是 pre-wrap 渲染，一句一行才是这个人的断句感；
       用句号拼接会把四条短句糊成一段，恰好抹掉要保住的东西。
    """
    # 保留原文标点：没有句末标点的片段是被截断的半句，不能当句子用
    raw_pieces = re.findall(r"[^。！？!?…\n]+[。！？!?…]?", text)
    parts = [p.strip() for p in raw_pieces if p.strip()]
    if parts and parts[-1][-1] not in "。！？!?…":
        # 末尾半句丢掉。留着会出现「…就成了他的。加一列」这种残句
        parts = parts[:-1]
    if not parts:
        return text.strip()

    limit = max(1, persona.sentence_max)
    long_idx = [i for i, p in enumerate(parts) if len(p) >= MIN_SENT_CHARS]

    if len(long_idx) <= limit:
        chosen = set(range(len(parts)))
    else:
        # 首句 + 末尾若干句；短碎片跟随它前面那句
        keep_idx = [long_idx[0], *long_idx[-(limit - 1) :]] if limit > 1 else long_idx[:1]
        chosen = set()
        for i, p in enumerate(parts):
            if i in keep_idx or (len(p) < MIN_SENT_CHARS and chosen and max(chosen) == i - 1):
                chosen.add(i)

    lines: list[str] = []
    for i, p in enumerate(parts):
        if i not in chosen:
            continue
        if len(p) < MIN_SENT_CHARS and lines:
            lines[-1] += p  # 断句碎片并入上一句，不单独占一行
            continue
        if p[-1] not in "。！？…":
            p += "。"
        lines.append(p)
    return "\n".join(lines)


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
