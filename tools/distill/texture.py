# -*- coding: utf-8 -*-
"""统计画像：把语料量成 `packages/persona/brufen/texture.json`。

测量对象（和后端加载器约定的口径必须一致）：
    bands.*       —— 分位整数。reply_chars 主口径是 his_replies（评论回复）；
                     sentences / clause_chars 仍量 dictation utterance。
    punctuation.* —— 0–1 占比，分母是 utterance 总数。
    conjunctions  —— 平均每个 utterance 里该词出现几次（浮点），不是百分比。
    self.*        —— 含「我」的 utterance 占比 + 每 100 个汉字里「我」的次数。
    openers[].share —— 该开头在 utterance 首 2 字位置上的占比。
    themes[].share  —— 该主题在 utterance 中的占比（和不必等于 1）。

为什么 numbers 全部由脚本算：
    这些数字唯一的用途是让打分器知道「一条回复离真实分布有多远」。
    手填一个「看起来合理」的中位数，等于把形容词换成了数字，下游还是蒙的。
    所以哪怕样本少、置信低，也要把 n 和局限一起写出来，让人能反驳。

三条口径提醒：
    1. 弹幕是观众打的字，utterance 是多个观众片段的拼接，不是逐字稿。
    2. 聚簇规则排除了以句末标点结尾的片段，所以 utterance 几乎没有句号，
       sentences band 会天然偏低——这是方法伪影，报告里单列。
    3. 3 份英文稿只做交叉校验，不回译、不进统计主体。
"""

from __future__ import annotations

import datetime as _dt
import re

from .corpus import hanzi_count

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

SENT_SPLIT = re.compile(r"[。！？!?…\n]+")
CLAUSE_SPLIT = re.compile(r"[，、；：,;:]")

# 口头禅候选：来自 persona.yaml 的 verbal_tics 与 docs 6.3。
# 这里的 n 是**原始弹幕**频次（不去重），因为要去核的就是 docs 记的那个数。
TIC_CANDIDATES = (
    "很简单", "必不可能", "腼腆", "速冻饺子", "甲方两眼一黑", "眼前一黑",
    "两眼一黑", "上岸", "下海", "说白", "倒霉蛋", "凉乞钞", "山田凉", "牛马",
)

# 主题词表。和 shotbank 的话题维度对齐（职场/家庭/感情/身体累/自我否定/钱/社交/荒唐提问），
# 另外补了语料里真实出现最多的学历、互联网两类。
THEME_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("职场", ("领导", "老板", "公司", "上班", "工作", "同事", "加班", "面试", "辞职",
              "甲方", "客户", "工资", "裁员", "打工", "工位", "下班")),
    ("家庭", ("我妈", "我爸", "父母", "催婚", "催生", "亲戚", "过年", "家里", "儿子",
              "女儿", "结婚", "家务", "相亲")),
    ("感情", ("喜欢", "恋爱", "女朋友", "男朋友", "对象", "分手", "前任", "结婚",
              "爱我", "爱情")),
    ("钱", ("钱", "房租", "贷款", "穷", "存款", "加班费", "工资", "账单", "押金",
            "罚款", "电费", "月光")),
    ("身体累", ("累", "困", "睡", "身体", "头疼", "难受", "撑", "疲惫", "熬夜", "失眠")),
    ("自我否定", ("没用", "失败", "不行", "废物", "没救", "算了", "丢人", "自卑", "瞧不起")),
    ("社交", ("朋友", "同学", "聚会", "社恐", "尴尬", "亲戚", "群聊", "没人")),
    ("荒唐提问", ("为什么", "怎么", "你会", "是不是", "假设", "如果", "能不能", "算不算")),
    ("学历", ("985", "211", "大学", "硕士", "本科", "学校", "学历", "毕业", "校招")),
    ("互联网", ("抖音", "微博", "知乎", "B站", "b站", "视频", "粉丝", "流量", "直播", "博主")),
]

# 成语/名篇参照，用于标题层「改写」判定。只收语料里出现过的原型 + 常见四字格，
# 不是为了穷举，而是为了让「改了一个字」这件事可以被机械判定。
IDIOM_PROTOTYPES = (
    "如获新生", "席卷全球", "小石潭记", "寻人启事", "狂人日记", "孔乙己",
    "捉妖记", "霸王别姬", "朝花夕拾", "出师表", "桃花源记", "岳阳楼记",
    "醉翁亭记", "一落千丈", "招摇撞骗", "无中生有", "人在囧途", "人在旅途",
    "喜从天降", "喜出望外", "天下无双", "后来居上", "前所未有", "不约而同",
)
# 自嘲标题的触发词。判定的是「标题有没有把自己放在低位」，不是好不好笑。
SELF_DEPRECATION_HINTS = (
    "躺平", "误入", "逼成", "素材", "霸凌", "盗窃", "搭讪", "当0", "当1",
    "废物", "失败", "没用", "牛马", "打工人", "老登", "耗材", "驴", "屎",
    "催婚", "催生", "被女友", "被男友", "我成了", "我遭遇了", "我自愿",
    "如获新牲", "喜卷", "小屎", "狂驴",
)


def percentile(values: list[int], p: float) -> int:
    """线性插值分位，返回整数。样本为空时返回 0（schema 要求字段不能删）。"""
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


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text or "") if s.strip()]


def split_clauses(text: str) -> list[str]:
    return [s.strip() for s in CLAUSE_SPLIT.split(text or "") if s.strip()]


def _ending(text: str) -> str:
    t = (text or "").rstrip()
    while t and t[-1] in "”’」』）)】》>\"'":
        t = t[:-1].rstrip()
    return t[-1:] if t else ""


def _bands(utterances: list[dict]) -> dict:
    chars = [hanzi_count(u["utterance"]) for u in utterances]
    sents = [len(split_sentences(u["utterance"])) for u in utterances]
    clauses: list[int] = []
    for u in utterances:
        clauses.extend(hanzi_count(c) for c in split_clauses(u["utterance"]))
    return {
        "reply_chars": {
            "p10": percentile(chars, 10),
            "p25": percentile(chars, 25),
            "p50": percentile(chars, 50),
            "p75": percentile(chars, 75),
            "p90": percentile(chars, 90),
        },
        "sentences": {
            "p10": percentile(sents, 10),
            "p50": percentile(sents, 50),
            "p90": percentile(sents, 90),
        },
        "clause_chars": {
            "p10": percentile(clauses, 10),
            "p50": percentile(clauses, 50),
            "p90": percentile(clauses, 90),
        },
    }


def _punctuation(utterances: list[dict]) -> dict:
    n = len(utterances)
    if not n:
        return {"period_ending_ratio": 0.0, "question_ratio": 0.0, "excl_ratio": 0.0, "ellipsis_ratio": 0.0}
    texts = [u["utterance"] for u in utterances]

    def ratio(pred) -> float:
        return round(sum(1 for t in texts if pred(t)) / n, 4)

    return {
        "period_ending_ratio": ratio(lambda t: _ending(t) == "。"),
        "question_ratio": ratio(lambda t: "？" in t or "?" in t),
        "excl_ratio": ratio(lambda t: "！" in t or "!" in t),
        "ellipsis_ratio": ratio(lambda t: "……" in t or "..." in t),
    }


def _conjunctions(utterances: list[dict]) -> dict:
    n = len(utterances)
    words = ("所以", "但是", "然后", "其实", "就是")
    if not n:
        return {w: 0.0 for w in words}
    return {w: round(sum(u["utterance"].count(w) for u in utterances) / n, 4) for w in words}


def _self(utterances: list[dict]) -> dict:
    n = len(utterances)
    total_i = sum(u["utterance"].count("我") for u in utterances)
    total_hanzi = sum(hanzi_count(u["utterance"]) for u in utterances)
    ratio = round(sum(1 for u in utterances if "我" in u["utterance"]) / n, 4) if n else 0.0
    per_100 = round(total_i / total_hanzi * 100, 2) if total_hanzi else 0.0
    return {"first_person_ratio": ratio, "first_person_per_100": per_100}


def _openers(utterances: list[dict], top: int = 40) -> list[dict]:
    """utterance 首 2 个汉字 = 一个开头。为什么是 2 字：中文开头的区分度基本在
    前两字（「我这 / 所以 / 不是 / 你就」），取 3 字会把「我今天」拆散。"""
    counts: dict[str, int] = {}
    total = 0
    for u in utterances:
        hanzi = "".join(CJK_RE.findall(u["utterance"]))
        if len(hanzi) < 2:
            continue
        opener = hanzi[:2]
        counts[opener] = counts.get(opener, 0) + 1
        total += 1
    rows = [
        {"text": text, "n": n, "share": round(n / total, 4) if total else 0.0}
        for text, n in counts.items()
    ]
    rows.sort(key=lambda r: (-r["n"], r["text"]))
    return rows[:top]


def _tics(raw_rows: list[tuple[str, float, str]]) -> list[dict]:
    """口头禅频次：在**原始弹幕**上数，不去重。

    为什么不去重：docs 6.3 记的「很简单 835」就是原始弹幕条数。
    去了重就只剩 1，没法核对。去重后的条数作为 note 一并给出。
    """
    out: list[dict] = []
    for word in TIC_CANDIDATES:
        hits = [(bv, t) for bv, t, text in raw_rows if word in text]
        # n<5 的不是「口头禅」，是孤例。口头禅的判据本来就要有频次（docs 6.3）。
        if len(hits) < 5:
            continue
        per_video: dict[str, list[float]] = {}
        for bv, t in hits:
            per_video.setdefault(bv, []).append(t)
        top_bv = max(per_video, key=lambda bv: len(per_video[bv]))
        top_times = sorted(per_video[top_bv])
        top_n = len(top_times)
        unique = len({text for _bv, _t, text in raw_rows if word in text})
        span = top_times[-1] - top_times[0]
        if top_n / len(hits) >= 0.8 and span <= 20:
            scope = "单支视频 %s %.1f–%.1fs 集中爆发" % (top_bv, top_times[0], top_times[-1])
        elif top_n / len(hits) >= 0.8:
            scope = "单支视频 %s，时间分散 %.1f–%.1fs" % (top_bv, top_times[0], top_times[-1])
        else:
            scope = "分散在 %d 支视频，最高一支占 %.0f%%" % (
                len(per_video),
                top_n / len(hits) * 100,
            )
        note = "原始弹幕 %d 条；去重后 %d 条写法" % (len(hits), unique)
        out.append({"word": word, "n": len(hits), "scope": scope, "note": note})
    out.sort(key=lambda r: -r["n"])
    return out[:40]


def _themes(utterances: list[dict]) -> list[dict]:
    """主题占比：命中该主题词表的 utterance 数 / 全部 utterance 数。"""
    n = len(utterances)
    if not n:
        return []
    out = []
    for name, keywords in THEME_KEYWORDS:
        hit_utts = [u["utterance"] for u in utterances if any(k in u["utterance"] for k in keywords)]
        if not hit_utts:
            continue
        kw_counts = {k: sum(1 for t in hit_utts if k in t) for k in keywords}
        top_kw = [k for k, c in sorted(kw_counts.items(), key=lambda x: -x[1]) if c > 0][:5]
        out.append(
            {
                "name": name,
                "share": round(len(hit_utts) / n, 4),
                "keywords": top_kw,
            }
        )
    out.sort(key=lambda r: -r["share"])
    return out[:40]


def _hamming1(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def _title_devices(titles: list[str]) -> dict:
    """标题层：成语改写 / 反差并列 / 自嘲。都是机械可判的计数，不做语义猜测。"""
    idiom = contrast = self_dep = 0
    for raw in titles:
        title = re.sub(r"[\s，,。！？!?、：:；;（）()「」『』《》\"'“”]", "", raw or "")
        hit_idiom = False
        for proto in IDIOM_PROTOTYPES:
            n = len(proto)
            for i in range(0, max(0, len(title) - n + 1)):
                if _hamming1(title[i : i + n], proto):
                    hit_idiom = True
                    break
            if hit_idiom:
                break
        idiom += int(hit_idiom)
        contrast += int(("，" in raw) or ("," in raw) or ("？" in raw))
        self_dep += int(any(h in raw for h in SELF_DEPRECATION_HINTS))
    return {
        "idiom_rewrite": idiom,
        "contrast_pair": contrast,
        "self_deprecation": self_dep,
        "n_titles": len(titles),
    }


def transcript_stats(transcripts: dict) -> dict:
    """3 份英文稿的结构量（词/句/语速/连接词/第一人称）。只进方法与报告，不进 bands。"""
    total_words = total_sents = total_sec = 0
    self_per_100: list[float] = []
    so_per_min: list[float] = []
    so_but_and: list[list[int]] = []
    for item in transcripts.values():
        words = item["words"]
        sents = item["sentences"]
        dur = item["duration"] or 0
        total_words += words
        total_sents += sents
        total_sec += dur
        if words:
            first = sum(len(re.findall(r"\bI\b", l["text"])) for l in item["lines"])
            self_per_100.append(first / words * 100)
        if dur:
            so = sum(1 for l in item["lines"] if re.match(r"^(so|and|but)\b", l["text"].strip(), re.I))
            n_so = sum(1 for l in item["lines"] if re.match(r"^so\b", l["text"].strip(), re.I))
            n_but = sum(1 for l in item["lines"] if re.match(r"^but\b", l["text"].strip(), re.I))
            n_and = sum(1 for l in item["lines"] if re.match(r"^and\b", l["text"].strip(), re.I))
            so_per_min.append(n_so / (dur / 60.0))
            so_but_and.append([n_so or 1, n_but, n_and])
    ratio = [0, 0, 0]
    for row in so_but_and:
        for i in range(3):
            ratio[i] += row[i]
    return {
        "words": total_words,
        "sentences": total_sents,
        "duration_sec": total_sec,
        "words_per_sec": round(total_words / total_sec, 2) if total_sec else 0.0,
        "self_per_100w_range": [round(min(self_per_100), 2), round(max(self_per_100), 2)] if self_per_100 else [0.0, 0.0],
        "so_per_min_range": [round(min(so_per_min), 2), round(max(so_per_min), 2)] if so_per_min else [0.0, 0.0],
        "so_but_and": ratio,
    }


def _his_section(his: dict) -> dict:
    """他只留下 schema 允许的字段；register 属于报告层，不进 texture。"""
    if not his:
        return {
            "n": 0,
            "chars": {"p10": 0, "p25": 0, "p50": 0, "p75": 0, "p90": 0},
            "sentences": {"p10": 0, "p50": 0, "p90": 0},
            "punctuation": {"period": 0.0, "question": 0.0, "excl": 0.0,
                            "ellipsis": 0.0, "tilde": 0.0, "no_end_punct": 0.0},
            "tics": [],
            "examples": [],
            "relation_to_parent": {"note": "未提取到 his_replies", "median_ratio": 0.0},
            "limits": ["未在 comments_full.json 里找到 mid=478720594 的评论"],
        }
    return {
        "n": int(his.get("n", 0)),
        "chars": dict(his.get("chars") or {}),
        "sentences": dict(his.get("sentences") or {}),
        "punctuation": dict(his.get("punctuation") or {}),
        "tics": list(his.get("tics") or [])[:40],
        "examples": list(his.get("examples") or [])[:40],
        "relation_to_parent": dict(his.get("relation_to_parent") or {}),
        "limits": list(his.get("limits") or []),
    }


def build_texture(
    *,
    utterances: list[dict],
    dictation_stats: dict,
    attention: dict,
    titles: list[str],
    transcripts: dict,
    timed: dict,
    comments_total: int,
    title_notes: list[str],
    his_replies: dict | None = None,
) -> dict:
    """组装 texture.json。所有字段都在这里出现，一个不缺。"""
    raw_rows = [
        (bv, float(t), text)
        for bv, item in timed.items()
        for t, text in item["rows"]
    ]
    truncated = sum(1 for item in timed.values() if len(item["rows"]) == 1200)
    unique_utterances = len({u["utterance"] for u in utterances})
    tstats = transcript_stats(transcripts)
    bands = _bands(utterances)
    dictation_reply_chars = dict(bands["reply_chars"])
    his = his_replies or {}
    his_n = int(his.get("n") or 0)
    # 主口径切换：评论回复是「一句话回一个人」，和聊天场景同构，比弹幕拼贴可信。
    # 只有可用样本 ≥100 才切；不足 100 时保留 dictation 口径并在 limits 里写明原因。
    if his_n >= 100 and his.get("chars"):
        bands["reply_chars"] = {
            "p10": int(his["chars"].get("p10", 0)),
            "p25": int(his["chars"].get("p25", 0)),
            "p50": int(his["chars"].get("p50", 0)),
            "p75": int(his["chars"].get("p75", 0)),
            "p90": int(his["chars"].get("p90", 0)),
        }
    skeletons = [
        {
            "skeleton": s["skeleton"],
            "n": int(s["n"]),
            "video": s["video"],
            "t": round(float(s["t"]), 1),
            "why": s["why"],
        }
        for s in attention["skeletons"][:40]
    ]
    limits = [
        "没有中文逐字稿；弹幕是观众边听边打的二手转写，不是他本人原话。",
        "%d 支视频的弹幕被 B 站 XML 接口截在 1200 条（任务书写 12，实测 %d），这些视频的 1 秒密度是抽样密度，不能跨视频比较。"
        % (truncated, truncated),
        "dictation 只有 %d 簇 / %d 条唯一 utterance；抽检显示多数簇是多个观众的吐槽拼接，band 的置信度低。"
        % (dictation_stats.get("clusters", 0), unique_utterances),
        "因为聚簇规则排除以句末标点结尾的片段，utterance 天然几乎不分句；sentences band 是方法伪影，不宜单独用于校准。",
        "55 个投稿标题在本机只留下 %d 个（space.html 是未水合的 JS 外壳），title_devices 的 n 远小于 docs 的 45 标题口径。"
        % len(titles),
        "comments(%d 条) 里混有他本人的 %d 条回复；其中 %d 条进入 his_replies，其余仍是观众语体，不进 bands。"
        % (comments_total, his_n, his_n),
        "his_replies 是评论区语体（短、有对象、有上下文），不是口播；不能拿它推他的断句和语速。",
        "his_replies 的 parent_text 由采集顺序推断，不是接口字段；relation_to_parent 只在可挂接的样本上算。",
    ]
    method_notes = [
        "弹幕时间轴聚簇是被迫代理：没有中文逐字稿，见 report.md 第 2 节",
        "bands.sentences / bands.clause_chars 量的是 dictation utterance（多个观众片段的时间拼接）；"
        "bands.reply_chars 已切换到 his_replies（他本人敲的评论回复）",
        "3 份英文稿共 %d 词 / %d 句 / %.1f 秒，只用于交叉校验，不回译、不进统计主体" % (
            tstats["words"], tstats["sentences"], tstats["duration_sec"]
        ),
        "所有比例是 0–1 小数；conjunctions 是平均每个 utterance 出现次数，不是百分比",
        "reply_chars 主口径已切换到 his_replies（n=%d，他本人敲的评论回复，一手中文）；dictation 口径 p50=%d 仅作对照"
        % (his_n, dictation_reply_chars.get("p50", 0)),
    ]
    return {
        "version": 1,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "videos": len(timed),
            "danmaku_total": sum(len(item["rows"]) for item in timed.values()),
            "danmaku_truncated_videos": truncated,
            "comments": int(comments_total),
            "titles": len(titles),
            "transcripts_en": len(transcripts),
            "dictation_clusters": int(dictation_stats.get("clusters", 0)),
            "dictation_utterances": int(unique_utterances),
            "method_notes": method_notes,
        },
        "bands": bands,
        "punctuation": _punctuation(utterances),
        "conjunctions": _conjunctions(utterances),
        "self": _self(utterances),
        "openers": _openers(utterances),
        "tics": _tics(raw_rows),
        "skeletons": skeletons,
        "themes": _themes(utterances),
        "title_devices": _title_devices(titles),
        "limits": limits,
        "his_replies": _his_section(his),
    }
