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

# 非严格模式下只记录、不阻断的问题类别。
# 这些都是「风格不够像助手」类，恰恰是最不该被机器改回来的部分。
_SOFT_PREFIXES = (
    "句数",
    "字数",
    "照抄了示例句",
    "每句都很长",
    "出现对称排比",
    "分句起头用填充词",
)


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


def longest_common_substring(a: str, b: str) -> int:
    """最长公共子串长度（滚动一维 DP）。

    用它而不是 `in`：实测里模型「抄」示例的方式不是逐字复制，而是改一两个字
    （示例的「我妈说过的是」变成「我妈说的是」）。精确子串匹配会把它判成原创，
    而人一眼就能看出那是同一句话。
    """
    if not a or not b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    best = 0
    for ch in a:
        cur = [0] * (len(b) + 1)
        for j, bj in enumerate(b, 1):
            if ch == bj:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def near_copy(text: str, lines: list[str], min_len: int = 10, ratio: float = 0.7) -> str | None:
    """判断 text 是否近似照抄了 lines 里的某一句。

    两个门槛同时成立才算抄：
      · 最长公共子串 ≥ min_len（10 字，短于此的常见搭配不算）
      · 该子串覆盖原句的 ≥ ratio（70%，允许改一两个字，不允许只借半句）

    为什么需要 ratio：只按长度判会把「我也这样想过」这类通用句全判成抄。
    """
    t = _norm_for_copy(text)
    if not t:
        return None
    for line in lines:
        n = _norm_for_copy(line)
        if len(n) < min_len:
            continue
        if n in t:
            return str(line).strip()
        # 先用 6 字窗口做一道廉价预筛：连一个 6 字片段都不重合，最长公共子串
        # 必然 < min_len，不用跑 DP。shotbank 有 200 多行样本，
        # 逐行跑 O(n·m) 的 DP 会让每个请求多花半秒，这道预筛把它压到几行。
        if not any(n[i : i + 6] in t for i in range(len(n) - 5)):
            continue
        lcs = longest_common_substring(n, t)
        if lcs >= min_len and lcs / len(n) >= ratio:
            return str(line).strip()
    return None


def copied_from_shots(text: str, persona: Persona, min_len: int = 12) -> str | None:
    """检查是否原样或近似抄了人格包里的示例句。

    这是个真实发生过的问题：给了 few_shots 之后，模型对「抢功劳」这类场景
    会直接输出示例的第一句，用户每问一次得到同一句话。示例是传节奏的，不是台词库。

    为什么阈值是 12 而不是 10：v2 用 10 时，一条完整的 21 字句子
    和一句 7 字口癖用的是同一个尺子，而后者本来就不该算抄。
    12 的意思是：整句搬运算抄，口头禅不算。

    v4 的补充：改成近似匹配（near_copy）。实测模型抄示例时会改一两个字，
    精确子串匹配放过了它——这比完全照抄更难被发现，因为改过之后读起来更像人话。
    """
    lines: list[str] = []
    for shot in persona.raw.get("few_shots") or []:
        lines.extend(str(shot.get("reply", "")).split("\n"))
    return near_copy(text, lines, min_len=min_len, ratio=0.7)


def check(
    text: str,
    persona: Persona,
    humor_scale: float = 1.0,
    relaxed: bool = False,
    strict: bool = True,
    min_chars: int = 0,
) -> QualityReport:
    """检查一条回复。

    strict=True：任何 issue 都算不合格，触发重写（v2 的默认行为）。
    strict=False：只拦「真的不能发」的问题（格式、助手腔、禁用词、严重超长），
    风格类问题只记录、不重写。

    v3 默认 strict=False。原因：重写这一步会把模型刚才那句「有点糙但像人」
    的话，改成一句「通顺但不像人」的话——它是在按规则改，而规则的边界
    一定比真实说话窄。风格窄化正是 AI 味的来源。
    """
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

    # 过短：用户讲了一整件事，回一句就完，读起来是敷衍。
    # 这不是风格问题——短是他的一部分，敷衍不是。所以在 raw 模式下也拦。
    if min_chars and n_char < min_chars:
        issues.append(f"回复过短（{n_char} 字，用户讲了一整件事）")

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

    if strict:
        return QualityReport(ok=not issues, issues=issues,
                             sentences=n_sent, chars=n_char, text=t)

    # 非严格模式：风格类只记录，不阻断。
    soft = tuple(i for i in issues if i.startswith(_SOFT_PREFIXES))
    blocking = [i for i in issues if i not in soft]
    # 长度仍然留一道底线：超过硬上限两倍，说明模型在写作文，必须压
    if n_char > char_limit * 2:
        blocking.append(f"字数 {n_char} 超过硬上限 {char_limit * 2}")
    if n_sent > sent_limit * 2:
        blocking.append(f"句数 {n_sent} 超过硬上限 {sent_limit * 2}")

    return QualityReport(
        ok=not blocking,
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


# 判据是「有没有一句以我开头」，不是词表。
# 先写过「我上回/我去年」，结果「我妈也干过」「我屋里那张桌子」全漏掉——
# 这类漏判是无限的：一个人能有一百种说法开头讲自己的事。
# 结构判据只有一条：某一句的主语是「我」，那就是在讲自己。
#
# 两个例外，都是**表态**而不是**叙事**：
#   「我觉得/我想/我认为…」——在给判断，没在讲经历；
#   「我不是…」——在否掉别人贴的标签（v3.1 的「否定排位」全靠这个句式），
#   它说的是「我是哪种人」，不是「我哪天干了什么」。
_SELF_OPINION = re.compile(r"^我((觉得|想|认为|知道|猜|估计|看|说)|(也)?不是)")


def self_story(text: str) -> bool:
    """这条回复有没有讲「我自己的那段经历」。

    用途不是评价好坏，而是防止连着两条都在讲自己。
    讲自己是这个人最好的一拍，但连着两轮就变成抢戏，
    而且会固化出「每条都有一个小故事」的新模板。

    「我觉得/我想/我看」这类是表态不是叙事，不算。
    """
    for seg in re.split(r"[。！？!?\n]+", text or ""):
        s = seg.strip().lstrip("（(「").strip()
        if len(s) >= 4 and s.startswith("我") and not _SELF_OPINION.match(s):
            return True
    return False


def extract_bits(text: str) -> list[str]:
    """提取「梗指纹」：每句开头几个字。

    目的不是精确识别梗，而是让模型别连续用同一种起手式——
    重复的句式比重复的梗更明显地暴露「这是个模板」。
    """
    bits: list[str] = []
    for s in SENT_SPLIT.split(text):
        s = s.strip()
        # 阈值 4：他的开头常只有四个字（「储物间。」「大家庭。」），6 字会全漏；
        # 但降到 3 会把「行。」「对。」也算进去，指令区被噪声占满，反而更糊。
        if len(s) >= 4:
            bits.append(s[:6])
    return bits[:5]
