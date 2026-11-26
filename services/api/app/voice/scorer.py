"""候选打分：同轮生成 N 条，按「离真实分布多远 + 有没有踩旧坑」挑一条。

为什么必须有这一层（而不是继续改 prompt）：

v3.1 的 docs/up-research.md 6.8.6 已经写明结论——
「剂量问题不该再用形容词去调。正确做法是同轮生成 3 条候选，
按『上一条有没有自述、长度是否在带内、有没有解释梗』打分挑一条」，
并且承认「OAD_CANDIDATES 配置项已存在，尚未实现挑选逻辑」。

v4 把这个洞补上了。它是这套系统里唯一一处「改完之后行为一定变」的地方：
prompt 改十次不如选样改一次，因为 prompt 只影响概率，选样直接改结果。

打分刻意做成**可解释的加减项**，不是黑箱模型：
每一个惩罚项都对应一次真实踩过的坑，能指到具体文档或测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import quality
from ..persona.loader import Persona
from .retrieval import sample_lines
from .texture import Texture, design_bands

# 判定为「不能发」的项：只要出现，这条候选直接出局。
# 阈值是 1000，而不是把各项权重调到刚好——出局就是出局，不参与边际比较。
_FATAL = 1000.0

# 「解释自己的梗」是 v3 第一轮实测（6 条里 5 条）暴露的问题：
# 他一辈子没说过「一个音」，直接说「上岸的反义词是下海」。
# 解释笑点等于当场宣布这是个套路，所以出现即出局。
_EXPLAIN_PUN_RE = re.compile(r"(一个音|同音字?|念起来像|读起来像|谐音是|这词(以前)?写作|押韵)")


@dataclass
class CandidateScore:
    text: str
    total: float = 0.0
    fatal: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    fit: dict[str, float] = field(default_factory=dict)
    # 形状标尺的来源：corpus（语料量出来的）/ design（人格包手写）。
    # 打出来是为了让「为什么这条被压短了」有答案，而不是只能猜。
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.fatal

    def summary(self) -> str:
        parts = [f"分 {self.total:.2f}"]
        if self.fatal:
            parts.append("出局：" + "；".join(self.fatal))
        if self.soft:
            parts.append("扣分：" + "；".join(self.soft))
        if self.fit:
            parts.append("形状：" + "、".join(f"{k} {v:+.2f}" for k, v in self.fit.items()))
        if self.sources and "design" in self.sources.values():
            parts.append("标尺：" + "/".join(f"{k}·{v}" for k, v in self.sources.items()))
        return " | ".join(parts)


def _count(text: str, needle: str) -> int:
    return text.count(needle) if needle else 0


def _hanzi_count(text: str) -> int:
    return len([c for c in text if c.strip()])


def _first_sentence(text: str) -> str:
    for seg in re.split(r"[。！？!?…\n]+", text or ""):
        s = seg.strip()
        if s:
            return s
    return ""


def _same_length_sentences(text: str) -> bool:
    parts = [p.strip() for p in re.split(r"[。！？!?…\n]+", text or "") if p.strip()]
    if len(parts) < 3:
        return False
    lens = [len(p) for p in parts]
    return max(lens) - min(lens) <= 2


def score(
    text: str,
    *,
    persona: Persona,
    texture: Texture,
    used_bits: list[str] | None = None,
    last_self: bool = False,
    user_text: str = "",
    last_reply_chars: int = 0,
) -> CandidateScore:
    """给一条候选打分。分数只用于同一轮候选之间的比较，绝对值没有意义。"""
    cs = CandidateScore(text=(text or "").strip())
    t = cs.text
    if not t:
        cs.fatal.append("空回复")
        return cs

    n_char = _hanzi_count(t)
    n_sent = quality.count_sentences(t)

    # ---- 出局项：格式与助手腔 ----
    if quality.EMOJI_RE.search(t):
        cs.fatal.append("emoji")
    if quality.MARKDOWN_RE.search(t) or quality.LIST_RE.search(t):
        cs.fatal.append("Markdown/列表")
    if quality.AI_TELL_RE.search(t):
        cs.fatal.append("助手腔")
    lowered = t.lower()
    for bit in persona.raw.get("anti_bits") or []:
        if bit and str(bit).lower() in lowered:
            cs.fatal.append(f"禁用词「{bit}」")
    for phrase in ((persona.raw.get("anti_ai_flavor") or {}).get("phrasings") or []):
        p = str(phrase).strip()
        if len(p) >= 4 and p.lower() in lowered:
            cs.fatal.append(f"助手腔「{p}」")
    if _EXPLAIN_PUN_RE.search(t):
        cs.fatal.append("解释了自己的梗")
    # 照抄检查跑两遍：人格包的 few_shots + 蒸馏出来的 shotbank。
    # 只查一个是不够的——v4 之后模型看到的大部分样本来自 shotbank，
    # 而它抄的正是最近看到的那些。
    copied = quality.copied_from_shots(t, persona) or quality.near_copy(
        t, sample_lines(persona), min_len=12, ratio=0.7
    )
    if copied:
        cs.fatal.append(f"照抄示例「{copied[:18]}」")

    # ---- 出局项：剂量与敷衍 ----
    # 连着两条讲自己 = 抢戏。单条讲自己是他最核心的一拍，不能碰。
    if last_self and quality.self_story(t):
        cs.fatal.append("上一条已在讲自己，这条又讲")
    # 用户讲了一整件事（≥10 字）却只回一句，是敷衍，不是「短是他的风格」。
    if len(re.sub(r"\s", "", user_text or "")) >= 10 and n_char < 12:
        cs.fatal.append(f"用户说满一句，只回 {n_char} 字")

    if not cs.ok:
        cs.total = -_FATAL * len(cs.fatal)
        return cs

    # ---- 形状拟合：离语料分布多远 ----
    # band 可能是语料量出来的，也可能是人格包的设计标尺。来源不做合并——
    # 语料代理靠不住的时候（比如弹幕聚簇量出来的句数是伪影），
    # 宁可回到设计标尺，也不要拿一个坏尺子去打分。
    design = design_bands(persona.raw)
    band_chars, chars_src = texture.effective("reply_chars", design)
    band_sent, sent_src = texture.effective("sentences", design)
    if band_chars:
        f = band_chars.fit(n_char)
        cs.fit["字数"] = f
        cs.total += 3.0 * f
    if band_sent:
        f = band_sent.fit(n_sent)
        cs.fit["句数"] = f
        cs.total += 2.0 * f
    cs.sources = {"chars": chars_src, "sentences": sent_src}

    # 标点：语料里问号很少（p90 都不高），堆问号是最像「客服」的习惯
    pun = texture.punctuation
    q = _count(t, "？") + _count(t, "?")
    if q:
        allowance = 1 if pun.get("question_ratio", 0.1) > 0.15 else 0
        if q > allowance:
            cs.total -= 0.8 * (q - allowance)
            cs.soft.append(f"问号 {q} 个（语料里问句占比 {pun.get('question_ratio', 0) * 100:.0f}%）")
    if _count(t, "！") + _count(t, "!") > 1:
        cs.total -= 0.6
        cs.soft.append("感叹号超过一个")
    if "……" in t or "..." in t:
        if pun.get("ellipsis_ratio", 0.05) < 0.05:
            cs.total -= 0.4
            cs.soft.append("用省略号装深沉")

    # 连接词剂量：所以是存在感最强、出场最少的一拍
    conj = texture.conjunctions
    expect_so = max(0.5, conj.get("所以", 0.3) * max(n_sent, 1) + 0.5)
    n_so = _count(t, "所以")
    if n_so > expect_so:
        cs.total -= 1.2 * (n_so - expect_so)
        cs.soft.append(f"「所以」{n_so} 次（期望 ≈{expect_so:.1f}）")

    # 第一人称过载：语料里本来就高，所以只在明显超出时扣
    self_ratio = texture.first_person_ratio
    if self_ratio and _count(t, "我") / max(n_sent, 1) > self_ratio * 4:
        cs.total -= 0.5
        cs.soft.append("每句都有「我」")

    # ---- 重复与模板 ----
    bits = {b[:4] for b in (used_bits or []) if b}
    first = _first_sentence(t)[:4]
    if first and first in bits:
        cs.total -= 1.0
        cs.soft.append(f"起手式「{first}」最近用过")
    if last_reply_chars and abs(n_char - last_reply_chars) <= 3:
        cs.total -= 0.5
        cs.soft.append("和上一条几乎一样长")

    # 「复述关键词。句号。」是他会做的动作，但连着用就是模板。
    if len(first) <= 6 and first and first in (user_text or ""):
        cs.total -= 0.7
        cs.soft.append(f"开头是裸复述「{first}」")
    if _same_length_sentences(t):
        cs.total -= 0.5
        cs.soft.append("句长太齐（像排比）")

    cs.total = round(cs.total, 3)
    return cs


def pick(
    candidates: list[str],
    *,
    persona: Persona,
    texture: Texture,
    used_bits: list[str] | None = None,
    last_self: bool = False,
    user_text: str = "",
    last_reply_chars: int = 0,
) -> tuple[str, list[CandidateScore]]:
    """在候选里挑分数最高的一条。全部出局时按「错误最轻」退回。

    退回策略是刻意的：与其让用户等一次重写，不如发一条形状不完美的话。
    重写会把「有点糙但像人」改成「通顺但不像人」——v3 已经吃过这个亏。

    退回时按「出局项最少」排序，不是按输入顺序。这条是实测补的：
    三条候选都照抄了示例时，按输入顺序会稳定发出第一条抄来的话，
    而三条里通常有一条只是「多了一个问号」。
    """
    scored = [
        score(
            c,
            persona=persona,
            texture=texture,
            used_bits=used_bits,
            last_self=last_self,
            user_text=user_text,
            last_reply_chars=last_reply_chars,
        )
        for c in candidates
    ]
    scored.sort(key=lambda s: (-len(s.fatal), -s.total))
    for s in scored:
        if s.ok and s.text:
            return s.text, scored
    for s in scored:
        if s.text:
            return s.text, scored
    return "", scored


# ---------------------------------------------------------------------------
# 候选多样性：同一轮 N 条候选如果只是换个词，选样就白做了。
# 这里给每条候选加一句形状指令，把分布撑开——不是让它「更creative」，
# 是让它覆盖真实对话里本来就有的长度与开口差异。
# ---------------------------------------------------------------------------
DIVERSITY_HINTS: list[str] = [
    "",
    "这一条只写一到两句，越短越好。不解释，不收尾。",
    "这一条不许用「我」开头，也不许讲你自己的经历。",
    "这一条可以落在你自己的一件具体小事上，但最后一句要回到他身上。",
]


def hint_for(index: int) -> str:
    return DIVERSITY_HINTS[index % len(DIVERSITY_HINTS)]
