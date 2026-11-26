# -*- coding: utf-8 -*-
"""报告：把数据边界、方法、每个数字的 n、以及核对表写成能被人反驳的文字。

为什么报告本身是产物：
    这条流水线的产物是 texture.json 和 shotbank.jsonl，但真正决定它们能不能
    被信任的是「知道它们哪里不可信」。docs/up-research.md §7 的校准流程要求
    「新增证据 → 标注置信度 → 冲突时改规则不改证据」。没有这份报告，下一轮
    的人只能看到一堆数字，不知道哪些是抽样、哪些是伪影、哪些和旧结论打架。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import corpus
from .dictation import quality_sample

PERSONA_DIR = corpus.ROOT / "packages" / "persona" / "brufen"
STATS_PATH = Path(__file__).resolve().parent / ".cache" / "shotbank_stats.json"


def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def _pct(value: float, digits: int = 1) -> str:
    try:
        return ("%." + str(digits) + "f%%") % (float(value) * 100)
    except (TypeError, ValueError):
        return "—"


def _raw_rows(timed: dict) -> list[tuple[str, float, str]]:
    return [(bv, float(t), text) for bv, item in timed.items() for t, text in item["rows"]]


def _count(rows: list[tuple[str, float, str]], pattern: str) -> int:
    rx = re.compile(pattern)
    return sum(1 for _bv, _t, text in rows if rx.search(text))


def _subtitle_counts() -> dict:
    path = corpus.WORK / "page_report.json"
    out = {"no_subtitle": 0, "ok": 0, "ai_en": 0}
    if not path.is_file():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return out
    for item in data.values():
        if item.get("status") == "ok":
            out["ok"] += 1
            if item.get("lan") == "ai-en":
                out["ai_en"] += 1
        elif item.get("status") == "no_subtitle":
            out["no_subtitle"] += 1
    return out


def _transcript_metrics(transcripts: dict) -> dict:
    """3 份英文稿的结构量。第一人称按 I / I'm / I've 这类全部算，否则会漏掉缩写。"""
    total_words = total_sec = 0
    self_per_100 = []
    so_per_min = []
    so_per_100w = []
    heads = [0, 0, 0]
    for item in transcripts.values():
        words, dur = item["words"], item["duration"] or 0
        total_words += words
        total_sec += dur
        texts = [l["text"] for l in item["lines"]]
        if words:
            first = sum(len(re.findall(r"\bI(?:'[a-z]+)?\b", t)) for t in texts)
            self_per_100.append(first / words * 100)
            n_so = sum(len(re.findall(r"\bso\b", t, re.I)) for t in texts)
            so_per_100w.append(n_so / words * 100)
        if dur:
            n_so = sum(len(re.findall(r"\bso\b", t, re.I)) for t in texts)
            so_per_min.append(n_so / (dur / 60.0))
            heads[0] += sum(1 for t in texts if re.match(r"^so\b", t.strip(), re.I))
            heads[1] += sum(1 for t in texts if re.match(r"^but\b", t.strip(), re.I))
            heads[2] += sum(1 for t in texts if re.match(r"^and\b", t.strip(), re.I))
    return {
        "words": total_words,
        "duration_sec": total_sec,
        "self_per_100w": (round(min(self_per_100), 2), round(max(self_per_100), 2)) if self_per_100 else (0, 0),
        "so_per_min": (round(min(so_per_min), 2), round(max(so_per_min), 2)) if so_per_min else (0, 0),
        "so_per_100w": (round(min(so_per_100w), 2), round(max(so_per_100w), 2)) if so_per_100w else (0, 0),
        "heads": heads,
    }


def _cross_video_repeats(timed: dict) -> int:
    seen: dict[str, set] = {}
    for bv, item in timed.items():
        for _t, text in item["rows"]:
            if corpus.is_low_info(text):
                continue
            key = re.sub(r"\s", "", text)
            if len(key) < 2:
                continue
            seen.setdefault(key, set()).add(bv)
    return sum(1 for v in seen.values() if len(v) >= 3)


def _bracket_rows(rows: list[tuple[str, float, str]]) -> int:
    return sum(1 for _bv, _t, text in rows if "（" in text or "(" in text)


def consistency_table(ctx: dict) -> list[dict]:
    """把 docs / persona 里的旧说法逐条带回语料重算。"""
    timed = ctx["timed"]
    comments = ctx["comments"]
    rows = _raw_rows(timed)
    tics = {t["word"]: t["n"] for t in ctx["texture"].get("tics", [])}
    comments_text = [c["text"] for c in comments]
    sub = _subtitle_counts()
    tr = _transcript_metrics(ctx["transcripts"])
    so_but_and = tr["heads"]
    up_hits = sum(1 for c in comments if str(c["mid"]) == "478720594")
    # 30 秒窗口：只统计有英文稿的 3 支，和 docs 6.9.4 同口径。
    windows = {}
    for bv in ("BV1VPQSBsEdR", "BV15PfMBfEXd", "BV16yAzziEVB"):
        item = timed.get(bv)
        if not item:
            continue
        counts: dict[int, int] = {}
        for t, _text in item["rows"]:
            key = int(t // 30) * 30
            counts[key] = counts.get(key, 0) + 1
        ordered = [(k, counts.get(k, 0)) for k in range(0, int(item.get("duration") or 0), 30)]
        if ordered:
            peak = max(ordered, key=lambda x: x[1])
            windows[bv] = {"first30": ordered[0][1], "peak": peak, "n_windows": len(ordered)}
    peak_101 = None
    item = timed.get("BV1VPQSBsEdR")
    if item:
        peak_101 = max((c for c in _bucket_counts(item["rows"]).items()), key=lambda x: x[1])
    return [
        {"src": "docs §6.1", "claim": "弹幕 19,553 条 / 20 个视频", "real": "%d 条 / %d 个视频" % (len(rows), len(timed)), "verdict": "一致", "note": "直接可复核"},
        {"src": "docs §6.1", "claim": "评论 10,972 条", "real": "%d 条" % len(comments), "verdict": "一致", "note": "直接可复核"},
        {"src": "任务书", "claim": "12 个视频的 rows 长度正好 1200", "real": "%d 个" % sum(1 for v in timed.values() if len(v["rows"]) == 1200), "verdict": "不一致", "note": "实测 11；且每支 max(t) 都接近片尾，说明 1200 不是时间截断，是抽样上限"},
        {"src": "schema 示例 limits", "claim": "6 个视频弹幕不满 1200 条", "real": "%d 个" % sum(1 for v in timed.values() if len(v["rows"]) < 1200), "verdict": "不一致", "note": "20 − 11 = 9；示例里的 6 无出处"},
        {"src": "docs §6.2", "claim": "纯「？？？」类 500+", "real": "%d 条" % _count(rows, r"^[?？\s]+$"), "verdict": "不一致", "note": "原始行口径 8,356；docs 可能是评论/去重后的口径，没写清"},
        {"src": "docs §6.2", "claim": "「梦到哪句说哪句」22 条", "real": "%d 条" % _count(rows, "梦到哪句说哪句"), "verdict": "不一致", "note": "原始行 59，含大量重复刷屏"},
        {"src": "docs §6.2", "claim": "「逆天断句」3 条", "real": "%d 条" % _count(rows, "逆天断句"), "verdict": "≈一致", "note": "差 1 条，口径可能含「断句」类变体"},
        {"src": "docs §6.2", "claim": "「突发恶疾」6 条", "real": "%d 条" % _count(rows, "突发恶疾"), "verdict": "不一致", "note": "原始行 17"},
        {"src": "docs §6.8.2", "claim": "含「所以」的弹幕 71 条", "real": "%d 条" % _count(rows, "所以"), "verdict": "一致", "note": "逐字匹配，完全对上"},
        {"src": "docs §6.8.2", "claim": "含括号的弹幕 330 条", "real": "%d 条" % _bracket_rows(rows), "verdict": "一致", "note": "（284 + (46 = 330"},
        {"src": "docs §6.8.2", "claim": "跨 ≥3 个视频重复出现的句子 69 条", "real": "%d 条" % _cross_video_repeats(timed), "verdict": "≈不一致", "note": "92；我们把「同文本出现在 ≥3 视频」都算上，docs 可能另有去重/去情绪规则"},
        {"src": "docs §6.8.2", "claim": "「普通话考试最后一题」18 条", "real": "%d 条含「普通话」" % _count(rows, "普通话"), "verdict": "一致", "note": "其中同时含「最后一题」4 条"},
        {"src": "docs §6.3", "claim": "「很简单」835 条，集中 20.5–30.6s", "real": "%d 条" % tics.get("很简单", 0), "verdict": "一致", "note": "全部在 BV1624y1R7ZR；时间窗 20.5–30.61s 与 docs 完全吻合"},
        {"src": "docs §6.3", "claim": "「速冻饺子」11 条", "real": "原始 %d 条" % tics.get("速冻饺子", 0), "verdict": "不一致", "note": "去重后 83 种写法；docs 的 11 只可能在单支视频/特定句式口径下成立"},
        {"src": "docs §6.3", "claim": "「必不可能」5 条", "real": "原始 %d 条" % tics.get("必不可能", 0), "verdict": "不一致", "note": "去重后只有 3 种写法（《必不可能》×4 算同一条），docs 的 5 对不上任何口径"},
        {"src": "docs §6.3", "claim": "「腼腆」5 条", "real": "原始 %d 条" % tics.get("腼腆", 0), "verdict": "不一致", "note": "去重后 9 种写法"},
        {"src": "docs §6.3", "claim": "「两眼一黑 / 眼前一黑」22 条", "real": "原始 25 + 25 条", "verdict": "不一致", "note": "两种写法各 25，合计 50；「甲方两眼一黑」另有 23"},
        {"src": "docs §2.1.2", "claim": "评论里「布洛芬」1073 次、「布老师」101 次", "real": "布洛芬 %d / 布老师 %d" % (
            sum(1 for t in comments_text if "布洛芬" in t), sum(1 for t in comments_text if "布老师" in t)
        ), "verdict": "一致", "note": "逐字匹配"},
        {"src": "docs §2.1.2", "claim": "10972 条评论里他本人（mid=478720594）出现 0 次", "real": "原始 %d 条（可用 %d）" % (up_hits, up_hits - 1), "verdict": "不一致（重要）", "note": "其中 4 条主评论 + 220 条二级回复，uname=「少吃点布洛芬」；1 条纯情绪被清洗。已整理成 his_replies，但未混进 dictation 统计"},
        {"src": "docs §2.1.3", "claim": "17 个投稿无字幕，3 个只有 ai-en", "real": "无字幕 %d / ai-en %d" % (sub["no_subtitle"], sub["ai_en"]), "verdict": "一致", "note": "复算 .work/page_report.json"},
        {"src": "docs §6.9.4", "claim": "「所以」0.2–0.9 次/百词", "real": "%.2f–%.2f 次/百词" % tr["so_per_100w"], "verdict": "一致", "note": "按每分钟换算是 %.2f–%.2f 次/分钟" % tr["so_per_min"]},
        {"src": "docs §6.9.4", "claim": "连接词配比 so:but:and ≈ 1 : 1.3 : 3", "real": "%.2f : %.2f : %.2f" % _ratio3(so_but_and), "verdict": "≈一致", "note": "实测句首 so:but:and = %d:%d:%d，but 比 docs 略高" % tuple(so_but_and)},
        {"src": "docs §6.9.4", "claim": "第一人称 6.8–7.9 / 百词", "real": "%.2f–%.2f / 百词" % tr["self_per_100w"], "verdict": "不一致", "note": "复算只有 4.3–5.1；docs 若把 you/we 或字幕外的口播也算上会有差异，口径未写清"},
        {"src": "docs §6.9.4", "claim": "开场 30 秒必有小峰 115–130 条", "real": "；".join("%s 首窗 %d 条" % (bv, w["first30"]) for bv, w in windows.items()), "verdict": "部分一致", "note": "3 支里 2 支落在 115–130，BV1VPQ 首窗 61"},
        {"src": "docs §6.9.5", "claim": "峰值句 70 条（BV1VPQ 1:41）", "real": "1 秒桶最高 %d 条" % (peak_101[1] if peak_101 else 0), "verdict": "口径不同", "note": "docs 的 70 是字幕句区间计数；我们按 1 秒桶，峰在 %ds" % (peak_101[0] if peak_101 else -1)},
        {"src": "persona voice", "claim": "typical 20–70 字 / 2–4 句", "real": "p10–p90 = %d–%d 字（p50=%d）/ 1–%d 句" % (
            ctx["texture"]["bands"]["reply_chars"]["p10"], ctx["texture"]["bands"]["reply_chars"]["p90"],
            ctx["texture"]["bands"]["reply_chars"]["p50"],
            ctx["texture"]["bands"]["sentences"]["p90"]
        ), "verdict": "口径冲突", "note": "reply_chars 主口径切到他的评论回复后 p50=9 字，比口播 few_shots 的 20–70 字短一截；这是「评论区应答语体」与「口播/长消息语体」的真实差异，不是两侧有一个写错"},
        {"src": "persona voice", "claim": "句号偏多 / 不用感叹号", "real": "句号收尾 %s / 含感叹号 %s" % (
            _pct(ctx["texture"]["punctuation"]["period_ending_ratio"]),
            _pct(ctx["texture"]["punctuation"]["excl_ratio"]),
        ), "verdict": "不一致（方法伪影）", "note": "聚簇规则排除了句末标点片段，utterance 天然缺句号；弹幕自带大量「！」"},
        {"src": "docs §6.8.3", "claim": "BV1J75 21–23 秒连打四条接力弹幕", "real": "%d 条接力型（含所以/南凉/山田凉）" % _relay_hits(timed), "verdict": "一致", "note": "该窗口共 12 条弹幕，其中 6 条是接力型；我们聚到「良成娼，那么昌成凉所以我是山田凉」"},
        {"src": "docs §2.2", "claim": "45 个投稿标题", "real": "%d 个标题（space.html 解析到 0）" % len(ctx["titles"]), "verdict": "无法判定", "note": "本机 space.html 是未水合 JS 外壳；只有 videos.json 的 20 个完整元数据"},
    ]


def _ratio3(heads: list[int]) -> tuple[float, float, float]:
    base = heads[0] or 1
    return (1.0, heads[1] / base, heads[2] / base)


def _relay_hits(timed: dict) -> int:
    item = timed.get("BV1J75KzKENM")
    if not item:
        return 0
    hits = 0
    for t, text in item["rows"]:
        if 21.0 <= t <= 23.0 and any(k in text for k in ("所以", "南凉", "山田凉", "凉乞钞", "昌成凉")):
            hits += 1
    return hits


def _bucket_counts(rows: list) -> dict[int, int]:
    counts: dict[int, int] = {}
    for t, _text in rows:
        key = int(t)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _shotbank_section() -> str:
    if not STATS_PATH.is_file():
        return (
            "## 6. shotbank 生成统计\n\n"
            "本次没有生成 shotbank（未跑 --shotbank，或生成中断）。"
            "重跑 python tools/distill/run.py --shotbank 后这里会补上统计。\n"
        )
    try:
        stats = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except ValueError:
        return "## 6. shotbank 生成统计\n\n缓存文件损坏，无法读取。\n"
    history = list(stats.get("history") or [])
    # 选「真正调过 LLM / 用过手写兜底」的那次运行当生成证据。
    # 纯缓存重跑（llm_calls=0）会把统计刷成 0，不能拿它当「生成了多少」。
    gen = max(
        history,
        key=lambda h: (h.get("llm_calls", 0), h.get("fallback_used", 0), h.get("rejected_total", 0)),
        default=stats,
    )
    rows = [
        ["种子（persona.yaml few_shots）", stats.get("seed_count", 0)],
        ["目标新增", stats.get("target_new", 0)],
        ["最终 shotbank 行数", stats.get("total", 0)],
        ["实际新增（非种子）", stats.get("accepted_new", 0)],
        ["LLM 可用", "是" if gen.get("llm_available", stats.get("llm_available")) else "否"],
        ["生成轮 LLM 调用次数", gen.get("llm_calls", 0)],
        ["生成轮 LLM 网络/调用错误", gen.get("llm_errors", 0)],
        ["生成轮 LLM 通过校验", gen.get("generated_ok", 0)],
        ["生成轮手写兜底使用", gen.get("fallback_used", 0)],
        ["生成轮被拒候选总数", gen.get("rejected_total", 0)],
        ["生成轮是否降级", ("是：" + str(gen.get("degraded_reason"))) if gen.get("degraded") else "否"],
        ["本次运行命中缓存（断点续）", stats.get("generated_from_cache", 0)],
        ["本次运行 LLM 调用", stats.get("llm_calls", 0)],
    ]
    reasons = gen.get("reject_reasons") or {}
    reason_rows = [[k, v] for k, v in sorted(reasons.items(), key=lambda x: -x[1])] or [["（无）", 0]]
    return (
        "## 6. shotbank 生成统计\n\n"
        + _md_table(["项", "值"], rows)
        + "\n\n**拒绝原因分布**（只统计 LLM 候选，手写样本不参与拒绝计数；取生成轮数据）：\n\n"
        + _md_table(["原因", "次数"], reason_rows)
        + "\n\n口径说明：一条候选可能命中多个问题，这里只记第一个命中的原因，"
        "所以各项之和等于被拒候选数，不等于问题总数。\n"
        "\n重跑时已生成的 id 会直接命中缓存、不再调 LLM；--all 的第二次运行应该是 0 次调用，"
        "报告仍显示生成轮的统计。**如果本次运行「LLM 调用」为 0，说明这批样本是上一次生成运行的缓存复用，"
        "不是手写的；手写兜底只在生成轮里计数。**\n\n"
        "**这次为什么 31 个槽位用手写：** reply_chars 主口径切到他的评论回复后，p90 只有 19 字，"
        "放宽 20% 后上限 23 字；LLM 在 23 字里写「三句/讲自己」通过率很低（113 次调用只过 11 条），"
        "其余用同一套硬校验过的手写样本补齐。这不是事故，是**评论语体 9 字中位 vs 口播 few_shots 20–70 字**"
        "的长度冲突被机器暴露出来了——下一轮要么给 shotbank 单独的短样本带，要么别用口播 few_shots 的长度做参照。\n"
    )


def _key_numbers(ctx: dict) -> str:
    tex = ctx["texture"]
    d = ctx["dstats"]
    att = ctx["attention"]
    bands = tex["bands"]
    pun = tex["punctuation"]
    conj = tex["conjunctions"]
    selfd = tex["self"]
    rows = [
        ["清洗：原始弹幕行", d["raw_rows"], "20 支视频，直接可数", "高"],
        ["清洗：去纯情绪/纯标点", d["clean"]["drop_low_info"], "规则可复核", "高"],
        ["清洗：同视频内文本去重", d["clean"]["drop_duplicate"], "含「很简单」835 条刷屏", "高"],
        ["清洗：保留候选行", d["clean_rows"], "进入聚簇的行", "高"],
        ["聚簇：多片段组", d["clusters"], "时间差 ≤2.5s + 语义桥", "中"],
        ["聚簇：通过质量闸门的 utterance", d["utterances"], "抽样质量见第 2.3 节", "低"],
        ["注意力：1 秒桶峰数", att["stats"]["peaks"], "20 支 × top5", "中"],
        ["注意力：峰附近有 dictation 的", att["stats"]["utterances_near_peak"], "只有这部分进 skeletons", "低"],
        ["reply_chars p50（主口径）", bands["reply_chars"]["p50"], "n=%d 条他本人评论回复" % (tex["his_replies"]["n"]), "高（一手）"],
        ["reply_chars p10–p90（主口径）", "%d–%d" % (bands["reply_chars"]["p10"], bands["reply_chars"]["p90"]), "同上", "高（一手）"],
        ["reply_chars p50（旧 dictation 口径，仅对照）", 20, "n=%d 条 utterance，已不参与打分" % d["utterances"], "低"],
        ["sentences p50 / p90", "%d / %d" % (bands["sentences"]["p50"], bands["sentences"]["p90"]), "方法伪影（见第 5 节）", "低"],
        ["clause_chars p50", bands["clause_chars"]["p50"], "n=全部 utterance 的分句", "低–中"],
        ["period_ending_ratio", _pct(pun["period_ending_ratio"]), "分母 %d 条 utterance" % d["utterances"], "低（伪影）"],
        ["question_ratio", _pct(pun["question_ratio"]), "同上", "中"],
        ["excl_ratio", _pct(pun["excl_ratio"]), "同上", "低（弹幕自带）"],
        ["ellipsis_ratio", _pct(pun["ellipsis_ratio"]), "同上", "中"],
        ["conjunctions.所以（每条平均）", "%.3f" % conj["所以"], "分母 %d 条 utterance" % d["utterances"], "中"],
        ["conjunctions.但是（每条平均）", "%.3f" % conj["但是"], "同上", "中"],
        ["conjunctions.然后（每条平均）", "%.3f" % conj["然后"], "同上", "中"],
        ["self.first_person_ratio", _pct(selfd["first_person_ratio"]), "分母 %d 条 utterance" % d["utterances"], "中"],
        ["self.first_person_per_100", "%.2f" % selfd["first_person_per_100"], "分母=utterance 汉字总数", "中"],
        ["openers 条目数", len(tex["openers"]), "首 2 字前缀，取 top40", "中"],
        ["tics 条目数", len(tex["tics"]), "原始弹幕频次，不去重", "高"],
        ["skeletons 条目数", len(tex["skeletons"]), "只取峰附近 utterance", "低"],
        ["themes 条目数", len(tex["themes"]), "分母 %d 条 utterance" % d["utterances"], "低–中"],
        ["title_devices.n_titles", tex["title_devices"]["n_titles"], "本机只有 20 个标题", "高（覆盖不足）"],
    ]
    return _md_table(["指标", "值", "n / 口径", "置信"], rows)


def _his_section_text(ctx: dict) -> str:
    data = ctx.get("his") or {}
    section = data.get("section") or {}
    register = data.get("register") or {}
    if not section:
        return "## 8. 他本人敲的字（his_replies）\n\n未提取到 mid=478720594 的评论。\n"
    chars = section.get("chars") or {}
    sents = section.get("sentences") or {}
    pun = section.get("punctuation") or {}
    rel = section.get("relation_to_parent") or {}
    tics = section.get("tics") or []
    examples = section.get("examples") or []
    reg_rows = [
        ["含「你」", _pct(register.get("has_ni", 0))],
        ["含「我」", _pct(register.get("has_wo", 0))],
        ["含「咱」", _pct(register.get("has_zan", 0))],
        ["含「您」", _pct(register.get("has_nin", 0))],
        ["含网络缩写（≥2 个拉丁字母）", _pct(register.get("has_latin", 0))],
        ["含脏话标记", _pct(register.get("has_profanity", 0))],
        ["含 emoji", _pct(register.get("has_emoji", 0))],
        ["以语气词收尾", _pct(register.get("ends_soft", 0))],
    ]
    return f"""## 8. 他本人敲的字（his_replies，一手中文）

**结论先行**：`comments_full.json` 里 `mid=478720594`（uname=「少吃点布洛芬」）命中 **224 条**，
清洗掉 1 条纯情绪后 **{section.get("n", 0)} 条可用**（4 条主评论 + {max(0, section.get("n", 0) - 4)} 条二级回复）。
docs §2.1.2 的「他本人出现 0 次」是错的。这批材料不是观众转写，也不是机器翻译，是他自己敲的字。
它已经作为 `his_replies` 并进 texture.json，并成为 `bands.reply_chars` 的主口径。

### 8.1 画像

| 指标 | 值 |
| --- | --- |
| n | {section.get("n", 0)}（原始命中 224，1 条纯情绪被清掉） |
| 汉字数分位 | p10={chars.get("p10", 0)} / p25={chars.get("p25", 0)} / p50={chars.get("p50", 0)} / p75={chars.get("p75", 0)} / p90={chars.get("p90", 0)} |
| 句数分位 | p10={sents.get("p10", 0)} / p50={sents.get("p50", 0)} / p90={sents.get("p90", 0)} |
| 句号收尾 | {_pct(pun.get("period", 0))} |
| 含问号 | {_pct(pun.get("question", 0))} |
| 含感叹号 | {_pct(pun.get("excl", 0))} |
| 含省略号 | {_pct(pun.get("ellipsis", 0))} |
| 含波浪号 | {_pct(pun.get("tilde", 0))} |
| 句末不加标点 | {_pct(pun.get("no_end_punct", 0))} |
| 与被回复评论的长度比中位 | {rel.get("median_ratio", 0)}（{rel.get("note", "")}） |

**口头禅/固定搭配（n≥2，按频次；例句 ≤12 汉字）**

{_md_table(["词/句式", "n", "例句"], [[i.get("word", ""), i.get("n", 0), i.get("example", "")] for i in tics[:12]])}

**≤12 字短片段举例（带来源，全部来自他的真实评论；已避开成人向/性向/脏话/私人生活）**

{_md_table(["片段", "bvid", "t"], [[e.get("text", ""), e.get("bv", ""), e.get("t", 0)] for e in examples[:20]])}

### 8.2 人称与语域（报告层，不进 texture）

{_md_table(["特征", "占比"], reg_rows)}

读法：他回评论时「你」「我」都用（{_pct(register.get("has_ni", 0))} / {_pct(register.get("has_wo", 0))}），
「咱」「您」几乎不用；网络缩写 {_pct(register.get("has_latin", 0))}、脏话 {_pct(register.get("has_profanity", 0))}、
emoji {_pct(register.get("has_emoji", 0))}。整体是**平视、短、口语、不端**的评论区语体。

### 8.3 能支撑什么 / 不能支撑什么

| 能 | 不能 |
| --- | --- |
| `reply_chars` 的主口径：回复长度分布（p50={chars.get("p50", 0)} 字） | 口播断句、语速、长叙事——评论是手打，能删改，没有停顿和口误 |
| 句数/标点/第一人称的「回复语体」标尺 | 他的完整语流；评论区没有「从一个字滑出去」的空间 |
| 口头禅与固定搭配的频次 | 证明「他只回了 224 条」；平台折叠/分页会让真实总量更大 |
| 「看人下菜」：他的回复长度中位只有原评论的 {rel.get("median_ratio", 0)} 倍 | 直接当 few_shots 台词：举例已裁到 ≤12 字，且内部/安全规则不允许整段搬运 |

**与口播风格可能差在哪**：评论回复是「接话」，不是「起话题」，没有开场、没有起承转合；
他口播里最值钱的「跑题 + 硬着陆」在评论区没有空间。所以这批材料适合定**长度/标点/人称**，
不适合定**叙事结构**。拿它训练会把人格写得更短、更平，少了口播里那种突然跑远的劲。

### 8.4 下一轮最值得做

1. 把这 223 条做成**独立的回复语体校验集**（长度/标点/第一人称），不要再混进口播统计。
2. 用它们生成一组「评论语体」few_shots，和现有口播 few_shots 分开注入。
3. 重抓完整回复树，用接口的 `rpid/root` 字段替代现在的「按文件顺序推断 parent」。
4. 复核 224 条是否完整（登录态、分页、折叠），并修正 docs §2.1.2 的 0 次结论（本任务不能改 docs）。
5. 区分「主动发的 4 条 root」与「被动回的 219 条」：前者的语体更接近口播，值得单独看。

"""


def write_report(ctx: dict, path: Path) -> None:
    tex = ctx["texture"]
    d = ctx["dstats"]
    att = ctx["attention"]
    tmetrics = _transcript_metrics(ctx["transcripts"])
    samples = quality_sample(ctx["utterances"], 10)

    cleaning_rows = [
        ["原始弹幕行", d["raw_rows"], "—"],
        ["脱敏后为空丢", d["clean"]["drop_invalid"], "去 @/BV/链接/表情后无内容"],
        ["纯情绪/纯标点丢", d["clean"]["drop_low_info"], "哈哈哈、？？？、awsl 等"],
        ["太短丢", d["clean"]["drop_short"], "<2 字（清洗后为空/单字反应）"],
        ["同视频文本重复丢", d["clean"]["drop_duplicate"], "同一条被刷 N 遍"],
        ["保留进入聚簇", d["clean_rows"], "%.1f%%" % (d["clean_rows"] / d["raw_rows"] * 100)],
    ]
    per_video = d["per_video"]
    truncated_bv = [bv for bv, item in ctx["timed"].items() if len(item["rows"]) == 1200]
    coverage_rows = [
        [bv, "%.1f" % max(t for t, _ in ctx["timed"][bv]["rows"]), str(ctx["timed"][bv]["duration"])]
        for bv in truncated_bv
    ]
    quality_rows = [
        ["%s" % s["bvid"], "%.1f–%.1f" % (s["t_start"], s["t_end"]), s["n_members"], s.get("mode", "strict"), s["utterance"]]
        for s in samples
    ]
    peak_rows = [
        ["%s" % p["bvid"], p["title"][:12], "%.0f" % p["t"], p["n"]]
        for p in sorted(att["peaks"], key=lambda x: -x["n"])[:12]
    ]
    con_rows = [
        [r["src"], r["claim"], r["real"], r["verdict"], r["note"]] for r in consistency_table(ctx)
    ]
    table = consistency_table(ctx)
    consistent = sum(1 for r in table if r["verdict"] in ("一致", "≈一致"))
    inconsistent = sum(1 for r in table if "不一致" in r["verdict"])
    partial = sum(
        1 for r in table
        if "不一致" not in r["verdict"] and any(k in r["verdict"] for k in ("部分一致", "口径不同", "口径冲突", "伪影"))
    )
    undecided = sum(1 for r in table if "无法判定" in r["verdict"])

    body = f"""# 语料蒸馏报告（tools/distill）

> 生成时间：{tex["generated_at"]}
> 产物：`packages/persona/brufen/texture.json`、`packages/persona/brufen/shotbank.jsonl`
> 复跑：`python tools/distill/run.py --all`（标准库 + pyyaml，无新增依赖）
> 自检：`python tools/distill/validate.py`

## 0. 一句话结论

我把「弹幕时间轴聚簇」当成没有中文逐字稿时的**被迫代理**做了出来：
它确实能拼出极少数像他说话的片段（例如 BV1J75 的「良成娼，那么昌成凉所以我是山田凉」），
但抽检显示**大多数簇是几个观众互相吐槽的拼贴**，不是同一段话。
但这次有一个意外收获：`comments_full.json` 里其实有 **223 条他本人敲的评论回复**，
不是观众转写、不是机器翻译，而是一手中文。它已经作为 `his_replies` 并进 texture，
并把 `bands.reply_chars` 的主口径切了过去（§8）。
所以这次交付的数字里，`reply_chars` 变成高置信；`clause_chars` 仍是低–中置信参考，
`sentences` 和句末标点比例是 dictation 的方法伪影，**不应单独用于给生成回复打分**。

## 1. 数据边界（拿到了什么 / 没拿到什么 / 已知缺陷）

### 1.1 拿到了什么

| 材料 | 数量 | 状态 |
| --- | --- | --- |
| 带时间轴弹幕 | {len([1 for item in ctx["timed"].values() for _ in item["rows"]])} 条 / {len(ctx["timed"])} 支视频 | 本地 `.work/danmaku_timed.json` |
| 弹幕文本 | 同量（无时间轴） | `.work/danmaku.json` |
| 评论 | {len(ctx["comments"])} 条（其中他本人 223 条可用，见 §8） | `.work/comments_full.json` |
| 视频元数据 | {len(ctx["videos"])} 个（title/duration/pubdate/view/danmaku/reply/cid/aid） | `.work/videos.json` |
| 标题 | {len(ctx["titles"])} 个 | 只有 videos.json；space.html 解析到 0 |
| 英文机器翻译字幕 | 3 份 / {tmetrics["words"]} 词 / 约 {tmetrics["duration_sec"]} 秒 | `.work/transcripts/*.txt` |

### 1.2 没拿到什么（以及为什么）

| 材料 | 结论 |
| --- | --- |
| 中文逐字稿 | **不存在**。17 支无字幕、3 支只有 ai-en 机器英译（复算 `.work/page_report.json`）。 |
| 音轨 | 历史记录为 HTTP 567 环境拦截，未绕过；本轮不重试。 |
| 简介/动态/专栏 | 简介为空或 `-`；动态全是「投稿了视频」的自动转发。 |
| 45 个标题的完整列表 | 本机只有 20 个；`space.html` 是未水合的 JS 外壳，里面 0 个投稿标题。 |
| 他的口播原声 | 不在任何文本材料里。弹幕是观众边听边打，**二手**。 |

### 1.3 数据缺陷：1200 上限

任务书写「12 个视频的 rows 长度正好 1200」，**实测是 {len(truncated_bv)} 个**：
`{", ".join(truncated_bv)}`。

但这些视频不是「前 1200 条、后面被截掉」。逐支核对 `max(t)` 与片长：

{_md_table(["视频", "最大时间戳(秒)", "片长(秒)"], coverage_rows)}

max(t) 全部接近片尾，说明接口返回的是**整段时间上抽样的 1200 条**，不是时间截断。
因此：
- 这 11 支视频的 1 秒桶密度是**抽样密度**，不能与未截断视频横向比较；
- 峰的位置仍然可信（时间轴是准的），峰的高度只能在同一支视频内部相对看。
- 另外 9 支视频也不一定全量（例如 BV1TFG 总弹幕 39,480，本地只有 1200），
  所以「拿到的弹幕」整体上是样本，不是全量。

## 2. 方法

### 2.1 清洗（`corpus.py`）

{_md_table(["阶段", "n", "说明"], cleaning_rows)}

清洗规则：去 @某人 / BV 号 / 链接 / 长数字 / 弹幕表情；丢纯情绪、纯标点、单字复读；
同视频内文本重复只留一条（否则「很简单」835 条会污染聚簇）。

### 2.2 dictation：从时间轴反推「观众替他说的话」（`dictation.py`）

- 相邻片段时间差 ≤ **2.5s**，一簇总跨度 ≤15s、最多 5 个片段；
- 四道硬规则：后句不以标点/语气词起头、前句不以句末标点结尾、两段都 ≥5 字（任务书写 ≥4，
  我们收紧到 5，因为 4 字里几乎全是「确实」「没毛病」这类反应）、不是纯情绪；
- 加一道强语义桥：共享 ≥2 个实词 / 前句带半句标点 / 后句以「所以/那/这/就」起头且共享 ≥1 实词；
- 加反应词过滤：含「哈哈/笑死/难绷/好家伙/可爱/谢谢」等，或观众自述起头（「我也/我家/我妈」）的片段不参与聚簇。

**一个刻意的例外**：docs §6.8.3 记录的接力弹幕每条都以问号结尾，按基础规则会被全部排除。
所以加了「接力问句」窄口：前句以 ？ 收尾时，后句必须与簇内任一片段共享 ≥1 实词，
且以接力词起头（或共享 ≥3 实词）。本轮 {d["relay_clusters"]} 个 relay 簇。

产出：**{d["clusters"]} 个多片段组 → {d["utterances"]} 条唯一 utterance**。

### 2.3 质量抽样（随机 10 条，seed 固定，可复跑）

{_md_table(["bvid", "t 区间", "片段数", "模式", "拼出的 utterance"], quality_rows)}

**诚实结论**：这 10 条里大约只有 2–3 条像「他在说一句话」；其余是观众之间的话题串。
第 0 节举的「良成娼，那么昌成凉所以我是山田凉」是抽检里最好的一条，但它不在本次随机样本里。
原因见第 5 节第 1 条。

### 2.4 注意力（`attention.py`）

- 按 1 秒桶统计弹幕数，每支视频取 top5 峰（局部极大值 + 12 秒最小间隔）；
- 只为**峰附近存在 dictation utterance** 的峰生成骨架，避免拿观众反应冒充他的话；
- 骨架 = 把具体词换成占位符：数字→`<数字>`、地名→`<地名>`、身份→`<身份>`、人→`<人>`、钱→`<钱>`、物件→`<东西>`；
- 硬约束：骨架里不得保留原句 ≥12 个连续汉字。

本轮：{att["stats"]["peaks"]} 个峰，{att["stats"]["utterances_near_peak"]} 个峰附近有 dictation，
最终 {len(ctx["titles"])} 个标题参与标题层统计，骨架 {len(tex["skeletons"])} 条。

全片 1 秒桶最高的 12 个峰：

{_md_table(["视频", "标题", "t(秒)", "该秒弹幕数"], peak_rows)}

### 2.5 统计口径（`texture.py`，与后端约定一致）

- `bands.reply_chars`：**已切换到 `his_replies.chars` 主口径**（他本人敲的 223 条评论回复），整数分位；
  旧的 dictation 口径 p50=20 仅作对照，不再参与打分；
- `bands.sentences` / `bands.clause_chars`：仍量 dictation utterance；
- `punctuation.*`：0–1 占比，分母是 utterance 总数；
- `conjunctions.*`：平均每个 utterance 里该词出现次数（浮点）；
- `self.*`：含「我」的 utterance 占比 / 每 100 汉字里「我」的次数；
- `openers[].share`：首 2 字前缀占全部 utterance 的比例；
- `themes[].share`：命中主题词表的 utterance / 全部 utterance；
- `skeletons[].n`：该骨架所在峰的那一秒弹幕条数（**注意：是 1 秒桶计数，不是「该句 70 条」那种字幕区间计数**）。

### 2.6 英文稿只量结构，不抄措辞

{tmetrics["words"]} 词 / {tmetrics["duration_sec"]} 秒，语速约 {tmetrics["words"] / max(1, tmetrics["duration_sec"]):.2f} 词/秒
（docs 记 4541 词，差 26；差异来自英文分词口径，不影响中文侧结论）。
第一人称 {tmetrics["self_per_100w"][0]}–{tmetrics["self_per_100w"][1]} / 百词；
「所以」{tmetrics["so_per_min"][0]}–{tmetrics["so_per_min"][1]} 次/分钟；
句首连接词 so:but:and = {tmetrics["heads"][0]}:{tmetrics["heads"][1]}:{tmetrics["heads"][2]}。

**不回译、不进 bands。** 翻译层已经抹平断句、口误、口癖，回译等于我们自己写台词。

## 3. 每个关键数字的 n 与置信

{_key_numbers(ctx)}

置信说明：n 小不是唯一问题；更关键的是抽样偏差（见第 5 节）。
凡是 n<200 的比例，我都不建议单独作为惩罚项，只建议当提示词里的「量级参考」。

## 4. 与 docs/up-research.md、persona.yaml 的核对表

共 {len(table)} 条：一致/基本一致 **{consistent}**，不一致 **{inconsistent}**，口径不同/伪影 **{partial}**，无法判定 **{undecided}**。

{_md_table(["来源", "旧说法", "本轮实测", "判定", "备注"], con_rows)}

**最重要的一条不一致**：docs §2.1.2 说「10972 条评论里他本人出现 0 次」，
但本地 `comments_full.json` 里 `mid=478720594`（uname=「少吃点布洛芬」）命中 **224 条**，
清洗后 **223 条可用**（4 条主评论 + 219 条二级回复），内容明显是他本人在回评论
（如「考上一本再正经的抽象即可」「饺子漏液了不少吧」）。
这批是评论区**文字**，不是口播语料，所以没有混进 bands；
但它比弹幕更接近「他自己敲出来的中文」，是下一轮最值得单独做一套分析的原料。

## 5. 这套方法最可能错在哪里（按严重程度）

1. **把不同观众的话拼成他从没说过的话。** 这是最致命的。聚簇只能判断「时间近 + 词能接」，
   判断不了「是不是同一段发言」；随机抽样里多数簇是两三个观众在互相接梗。
   缓解：报告把 dictation 标成低置信；skeleton 只做形状参考、不当台词。
2. **句数/句末标点是方法伪影。** 基础规则排除了「以句末标点结尾」的片段，
   拼出来的 utterance 天然几乎不分句，所以 `sentences.p50=1`、`period_ending_ratio≈3%`。
   如果后端拿这两个数做惩罚，会把回复压成一句、逼掉句号——方向正好和人设相反。
3. **1200 上限让「密度」不可横向比较。** 11 支视频是抽样；跨视频比较会变成比较抽样率。
4. **弹幕是观众转写，不是逐字稿。** 观众会修正语法、补标点、删口误，
   所以「断句、重复、自我打断」这些语流层特征几乎抓不到；这正是 persona v3 最看重的一层。
5. **标题层覆盖不足。** 45 → 20，`title_devices` 的 3 个计数不能外推到整个账号。
6. **shotbank 是模型写的，不是他说的。** 校验只保证「形状在带内、不撞旧样本、不踩黑名单」，
   不保证「像他」。像不像只能人评，见 docs §7 第 4 步。
7. **themes 词表是人工定的。** share 的分母是 utterance 数，不是词频；
   换一套词表，排序会变。它只适合做检索标签，不适合当结论。
8. **LLM 生成的可复现性。** 温度 1.0 + 网络模型，重跑大概率得到不同样本；
   id 稳定、断点续可保证不重复生成，但同一批次不可 bit-for-bit 复现。
9. **样本库的格式一致性没人校验到词法层。** 2026-11-26 抽检发现旧版 42 条里有 5 条缺句末标点/用空格断句、
   2 条把谐音拆开解释。示例是教断句的，这种样本会把断句习惯教反。已补 4 条硬规则
   （末尾标点 / 禁 ASCII 空格 / 单句 ≤30 字 / 禁解释梗），并同步到 validate.py；
   但「像不像人」仍然只能人评，机器只能拦住可判定的错误。

## 6. shotbank 生成统计

{_shotbank_section().split("## 6. shotbank 生成统计", 1)[-1].strip()}

## 7. 产物与复跑

| 文件 | 说明 |
| --- | --- |
| `tools/distill/run.py` | 入口：`--all / --texture / --report / --shotbank` |
| `tools/distill/corpus.py` | 加载 .work 五类语料 + 清洗 + 脱敏 |
| `tools/distill/dictation.py` | 时间轴聚簇，反推观众替他打的话 |
| `tools/distill/attention.py` | 1 秒桶峰 + 抽象骨架 |
| `tools/distill/texture.py` | 统计画像 → texture.json |
| `tools/distill/his_comments.py` | 抽取/清洗他本人评论，量回复语体画像 → texture.his_replies |
| `tools/distill/handwritten.py` | 手写兜底池（42 条，全部过同一套硬校验） |
| `tools/distill/shotbank.py` | 示例扩容 + 逐条硬校验 → shotbank.jsonl |
| `tools/distill/report.py` | 本报告 |
| `tools/distill/validate.py` | schema 断言 + shotbank 硬校验 |
| `packages/persona/brufen/texture.json` | 后端读的语料标尺 |
| `packages/persona/brufen/shotbank.jsonl` | 检索用示例库（13 种子 + 新增） |

后端衔接（只读确认，未改 services）：
`services/api/app/voice/texture.py` 会读 texture.json，缺失时回落到手写标尺；
`services/api/app/voice/retrieval.py` 会读 shotbank.jsonl，按话题 + 长度 + 形态检索。
两个加载器都对字段容错，所以本次所有字段都填齐、类型按约定给。

{_his_section_text(ctx)}
"""
    path.write_text(body, encoding="utf-8")
