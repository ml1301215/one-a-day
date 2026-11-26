# -*- coding: utf-8 -*-
"""他本人敲的字：从 10972 条评论里抽出 mid=478720594 的 224 条。

为什么这是本机最重要的一份语料：
    弹幕是观众边听边打、被观众修正过的二手转写；英文稿是被机器翻译抹平的三手材料。
    只有这 224 条是**他自己敲出来的中文**，而且场景就是「一句话回一个人」——
    和聊天人格要生成的东西同构。docs §2.1.2 说「他本人出现 0 次」是错的，
    本模块的第一句结论就是把这个事实钉在代码里。

这份材料不能支撑什么：
    它是**评论区语体**，不是口播语体。评论回复短、有对象、有上下文，
    不需要自己起话题，也不必铺垫；所以它适合定「回复长度/标点/第一人称」，
    不适合定「他怎么讲一件完整的事」。报告 §8 会把这条边界写死。

隐私与产物边界：
    统计结果进 texture.json；原句只以 ≤12 字的短片段举例（带 bv + 秒/时间戳）。
    不整段搬运，不写进任何 prompt。
"""

from __future__ import annotations

import re

from .corpus import CJK_RE, content_len, hanzi_count, is_low_info

UP_MID = "478720594"
MAX_SHORT_ECHO = 12  # 举例最多 12 个汉字，硬约束

SENT_SPLIT = re.compile(r"[。！？!?…\n]+")
CLAUSE_SPLIT = re.compile(r"[，、；：,;:]")
# 平台把「回复 @某人 :」写进正文。它不是他的话，统计前必须剥掉。
REPLY_PREFIX_RE = re.compile(r"^回复\s*@[^:：]{1,24}\s*[:：]\s*")

# 口头禅候选：既有 persona 收录的，也有评论区高频固定搭配。
TIC_CANDIDATES = (
    "很简单", "必不可能", "腼腆", "速冻饺子", "两眼一黑", "眼前一黑", "说白",
    "倒霉蛋", "牛马", "确实", "可以", "就是", "不是", "没有", "那我", "你要",
    "真的", "行吧", "好家伙", "你看", "我说", "你说", "我是", "什么", "怎么",
    "因为", "所以", "但是", "然后", "其实", "给你", "这玩意", "那玩意", "笑死",
    "绷不住", "离谱", "确实", "懂了", "学到了", "好耶", "行", "没有没有",
)

PROFANITY_RE = re.compile(r"(卧槽|我靠|特么|他妈|妈的|傻逼|傻|滚|操|艹|草|fuck|shit|tmd|md)")

# 举例/口头禅例句的安全过滤。这份产物会进人格包，项目硬规则明确不复刻
# 成人向、性暗示、性向调侃和私人生活；他的评论区里确实有这类内容，
# 统计可以算，例句不能引。
UNSAFE_EXAMPLE_RE = re.compile(
    r"(老二|直男|女优|性|做爱|炮|裸|胸|腿|屁股|屎|尿|操|他妈|妈的|傻逼|傻"
    r"|死|杀|病|药|字母|早谢|早泄|约|前女友|前男友|女朋友|男朋友|老婆|老公)"
)
LATIN_RE = re.compile(r"[A-Za-z]{2,}")
EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U0001F1E6-\U0001F1FF" "\u2600-\u27BF" "\uFE0F" "\u2B00-\u2BFF" "]"
)
SOFT_END = set("啊吧呢嘛呗哦呀啦哈咯噢喔")
NO_END_PUNCT = set("。！？!?…~～，、；：,;:")

# 人工挑的举例片段：全部来自他的真实评论，≤12 汉字，避开了成人向/性向/脏话/
# 私人生活。自动挑选能保证「确实是他说的」，但保证不了「适合放进人格包」，
# 所以最后一道用这份清单兜底，脚本会回到 224 条里核对片段存在且取来源 bv+t。
CURATED_EXAMPLES = (
    "考上一本再正经的抽象即可",
    "饺子漏液了不少吧",
    "没有关税，因为我与你无关",
    "所有话都是假的",
    "又说错了，是马斯克写的",
    "咱俩是拼好帅吗",
    "我这就预约近视手术",
    "你的意思是我不出场",
    "头没那么正",
    "工作前一天",
    "是亲身经历的一半",
    "老师，没事干可以去支教",
    "客户是奇葩的东西",
    "那我要推荐弯仔马头",
    "你像对待段子一样对待感情",
    "之前一直忙着找猫",
    "他们不把我当家人",
    "内容与事实不符",
    "令君荀彧，希望你早日康复",
    "速冻饺子是我",
    "那我给你抖音",
    "那我完了",
)


def _strip_reply_prefix(text: str) -> str:
    return REPLY_PREFIX_RE.sub("", text or "").strip()


def _clean_text(text: str) -> str:
    """只做平台前缀剥离与空白归一，其它原样保留。

    为什么不脱敏 @/BV/链接：这批是「他怎么说话」的一手材料，脱敏会顺带
    删掉他真实的用词（比如 @ 别人时的语气）。它不会进 prompt，风险可控。
    """
    t = _strip_reply_prefix(text)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t\u3000]+", " ", t)
    return t.strip()


def _ending(text: str) -> str:
    t = (text or "").rstrip()
    while t and t[-1] in "”’」』）)】》>\"'":
        t = t[:-1].rstrip()
    return t[-1:] if t else ""


def _short_echo(text: str, center_word: str | None = None) -> str:
    """把一条评论压成 ≤12 个汉字的举例。优先围绕命中词取窗口。"""
    t = re.sub(r"\s+", "", text or "")
    t = re.sub(r"^[《「『“\"'(（]+|[》」』”\"')）]+$", "", t)
    hanzi = "".join(CJK_RE.findall(t))
    if len(hanzi) <= MAX_SHORT_ECHO:
        return hanzi
    if center_word and center_word in hanzi:
        i = hanzi.find(center_word)
        start = max(0, min(i - 2, len(hanzi) - MAX_SHORT_ECHO))
        return hanzi[start : start + MAX_SHORT_ECHO]
    return hanzi[:MAX_SHORT_ECHO]


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    vals = sorted(float(v) for v in values)
    if len(vals) == 1:
        return int(round(vals[0]))
    k = (len(vals) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    frac = k - lo
    return int(round(vals[lo] + (vals[hi] - vals[lo]) * frac))


def _attach_parents(comments: list[dict]) -> list[dict]:
    """按文件顺序给二级回复挂主评论。

    采集器的组装顺序是「主评论 → 它的所有二级回复 → 下一条主评论」，
    所以每条二级回复之前最近的那条主评论就是它的父评论。这是**推断**，
    不是接口字段——报告里要把这条写进 limits。
    """
    rows = []
    last_root: dict | None = None
    for c in comments:
        if str(c.get("mid")) != UP_MID:
            if c.get("layer") == 1:
                last_root = c
            continue
        text = _clean_text(c.get("text") or "")
        if is_low_info(text) or content_len(text) == 0:
            continue
        layer = c.get("layer")
        parent_text = None
        if layer == 2 and last_root is not None:
            parent_text = _clean_text(last_root.get("text") or "")
        rows.append(
            {
                "bv": str(c.get("bv") or ""),
                "layer": layer,
                "t": int(c.get("t") or 0),
                "text": text,
                "parent_text": parent_text,
                "like": int(c.get("like") or 0),
                "chars": hanzi_count(text),
            }
        )
    return rows


def _tics(rows: list[dict]) -> list[dict]:
    """他自己反复用的词/句式。n≥2 才收录，排序按频次。"""
    out = []
    seen_words: set[str] = set()
    for word in TIC_CANDIDATES:
        if word in seen_words:
            continue
        hits = [r for r in rows if word in r["text"]]
        if len(hits) < 2:
            continue
        seen_words.add(word)
        # 例子选最短、且不超过 12 个汉字的一条；必须过安全过滤。
        safe_hits = [r for r in hits if not UNSAFE_EXAMPLE_RE.search(r["text"])]
        if not safe_hits:
            continue
        example_row = min(safe_hits, key=lambda r: (r["chars"], len(r["text"])))
        out.append({"word": word, "n": len(hits), "example": _short_echo(example_row["text"], word)})
    out.sort(key=lambda r: (-r["n"], r["word"]))
    return out[:40]


def _register(rows: list[dict]) -> dict:
    """人称与语域：这些不进 texture 的 schema，只写报告。

    为什么单独算：评论语体比口播更容易出现「你」「咱」和网络缩写；
    如果直接拿它定人格，会把聊天写得太「评论区」。这份数字是给下一轮
    做权重时看的，不是结论。
    """
    n = len(rows)
    if not n:
        return {}

    def ratio(pred) -> float:
        return round(sum(1 for r in rows if pred(r["text"])) / n, 4)

    return {
        "n": n,
        "has_ni": ratio(lambda t: "你" in t),
        "has_wo": ratio(lambda t: "我" in t),
        "has_zan": ratio(lambda t: "咱" in t),
        "has_nin": ratio(lambda t: "您" in t),
        "has_latin": ratio(lambda t: bool(LATIN_RE.search(t))),
        "has_profanity": ratio(lambda t: bool(PROFANITY_RE.search(t))),
        "has_emoji": ratio(lambda t: bool(EMOJI_RE.search(t))),
        "ends_soft": ratio(lambda t: _ending(t) in SOFT_END),
    }


def build(comments: list[dict]) -> dict:
    """返回 {section, register, rows}。section 直接并进 texture.json。"""
    rows = _attach_parents(comments)
    n = len(rows)
    chars = [r["chars"] for r in rows]
    sentences = [len([s for s in SENT_SPLIT.split(r["text"]) if s.strip()]) for r in rows]
    clauses: list[int] = []
    for r in rows:
        clauses.extend(hanzi_count(c) for c in CLAUSE_SPLIT.split(r["text"]) if c.strip())

    def ratio(pred) -> float:
        return round(sum(1 for r in rows if pred(r["text"])) / n, 4) if n else 0.0

    punct = {
        "period": ratio(lambda t: _ending(t) == "。"),
        "question": ratio(lambda t: "？" in t or "?" in t),
        "excl": ratio(lambda t: "！" in t or "!" in t),
        "ellipsis": ratio(lambda t: "……" in t or "..." in t),
        "tilde": ratio(lambda t: "~" in t or "～" in t),
        "no_end_punct": ratio(lambda t: (_ending(t) not in NO_END_PUNCT) and bool(_ending(t))),
    }

    # 长度关系：他回得比原评论长还是短。
    ratios: list[float] = []
    for r in rows:
        parent = r.get("parent_text") or ""
        p_chars = hanzi_count(parent)
        if p_chars >= 2 and r["chars"] > 0:
            ratios.append(r["chars"] / p_chars)
    ratios.sort()
    median_ratio = round(ratios[len(ratios) // 2], 3) if ratios else 0.0
    relation_note = (
        "parent_text 由「同一主评论的回复紧跟在主评论之后」的采集顺序推断，不是接口字段；"
        "可用配对 n=%d。median_ratio=他的汉字数/被回复评论汉字数，>1 表示他回得比对方长。"
        % len(ratios)
    )

    # 举例：先放人工挑过的安全片段（回到 224 条里核对存在与来源），
    # 不足 10 条时才用自动挑选补足。
    picked = []
    seen: set[str] = set()
    for fragment in CURATED_EXAMPLES:
        for r in rows:
            if fragment in r["text"] and fragment not in seen:
                seen.add(fragment)
                picked.append({"text": fragment, "bv": r["bv"], "t": r["t"]})
                break
    if len(picked) < 10:
        for r in sorted(rows, key=lambda r: (-r["like"], r["chars"])):
            if UNSAFE_EXAMPLE_RE.search(r["text"]):
                continue
            echo = _short_echo(r["text"])
            if len(CJK_RE.findall(echo)) < 4 or len(CJK_RE.findall(echo)) > MAX_SHORT_ECHO:
                continue
            if is_low_info(echo) or echo in seen:
                continue
            seen.add(echo)
            picked.append({"text": echo, "bv": r["bv"], "t": r["t"]})
            if len(picked) >= 20:
                break

    section = {
        "n": n,
        "chars": {
            "p10": percentile(chars, 10),
            "p25": percentile(chars, 25),
            "p50": percentile(chars, 50),
            "p75": percentile(chars, 75),
            "p90": percentile(chars, 90),
        },
        "sentences": {
            "p10": percentile(sentences, 10),
            "p50": percentile(sentences, 50),
            "p90": percentile(sentences, 90),
        },
        "punctuation": punct,
        "tics": _tics(rows),
        "examples": picked,
        "relation_to_parent": {"note": relation_note, "median_ratio": median_ratio},
        "limits": [
            "评论区语体，不是口播语体；短、有对象、有上下文，不能拿来推他的断句和语速。",
            "parent_text 是按采集顺序推断的，不是接口字段；%d 条没挂上父评论。" % (n - len(ratios)),
            "原始命中 224 条，清洗掉 1 条纯情绪后可用 %d 条；平台折叠/排序会让它不等于「他所有回复」。"
            % n,
            "有 4 条是他发的主评论（没有父评论），已计入 n。",
            "原句只以 ≤12 汉字短片段举例，带 bv + 时间戳；不进入任何 prompt。",
        ],
    }
    return {"section": section, "register": _register(rows), "rows": rows}


def summarize(data: dict) -> str:
    s = data["section"]
    lines = [
        "他本人评论：n=%d（4 主评论 + %d 二级回复）" % (s["n"], s["n"] - 4),
        "字数分位：p10=%d p25=%d p50=%d p75=%d p90=%d"
        % (s["chars"]["p10"], s["chars"]["p25"], s["chars"]["p50"], s["chars"]["p75"], s["chars"]["p90"]),
        "句数分位：p10=%d p50=%d p90=%d"
        % (s["sentences"]["p10"], s["sentences"]["p50"], s["sentences"]["p90"]),
        "标点：" + " ".join("%s=%.3f" % (k, v) for k, v in s["punctuation"].items()),
        "被回复长度比中位：%.3f（n=%s）" % (
            s["relation_to_parent"]["median_ratio"],
            s["relation_to_parent"]["note"].split("n=")[-1].split("。")[0],
        ),
        "口头禅 top5：" + "、".join("%s×%d" % (t["word"], t["n"]) for t in s["tics"][:5]),
    ]
    return "\n".join(lines)
