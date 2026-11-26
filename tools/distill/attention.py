# -*- coding: utf-8 -*-
"""注意力：观众在哪儿同时动手打字，以及那些话长什么样。

为什么按 1 秒桶：
    弹幕时间戳是「发送时的播放进度」，不是墙钟时间。同一秒里出现多少条，
    就是那一刻有多少人同时停下来打字——这是这个项目能拿到的最原始的
    「注意力计量」。比播放量、点赞数都更贴近「哪句话让人有反应」。

为什么只做同一支视频内部的峰：
    11 支视频的弹幕被接口截到 1200 条，是抽样，不是全量。跨视频比密度
    会把「抽样率不同」误读成「观众更激动」。所以峰只在一个视频内部排名。

骨架从哪来（这一点和第一版不同）：
    只从 dictation 聚簇里出，不从「窗口内最高频弹幕」出。第一版用高频
    弹幕兜底，结果骨架全是「开幕雷击」「突发恶疾」这类观众反应——
    那是观众在评价，不是他在说话。宁可骨架少，也不要拿观众的话冒充他。
    没有 dictation 簇覆盖的峰，只进报告的峰表，不进 texture.json。

骨架是什么：
    把峰值附近的话里的具体词换成占位符（985 → <数字>，义乌 → <地名>），
    留下的是一句话的逻辑形状，不是他说过的原句。骨架给人看，用来判断
    「观众到底在为哪一种句子动手」，不能拿去当台词。
"""

from __future__ import annotations

import re

from .corpus import content_len, hanzi_count, is_low_info, load_timed_danmaku

# 1 秒桶。更细的桶会被发送抖动放大，更粗的桶会把相邻两个峰糊成一个。
BUCKET = 1.0
TOP_PEAKS = 5
# 两个峰至少隔这么远才算两个峰；否则同一段爆发会被数成 top1/top2。
PEAK_MIN_SEPARATION = 12.0
# 找 utterance 的窗口：峰前后各 5 秒。取 5 秒而不是 2.5，是因为聚簇本身
# 已经要求相邻片段 ≤2.5 秒，再按 2.5 秒卡峰会系统性漏掉「刚说完那半句」。
WINDOW = 5.0
MAX_SKELETONS = 40

_DIGIT_RE = re.compile(r"\d+(?:\.\d+)?%?")
_CN_NUM_WITH_UNIT_RE = re.compile(
    r"[一二三四五六七八九十百千万亿两几半]+(?=(?:个|条|块|元|岁|天|年|月|日|分钟|小时|次|回|遍|万|百|千|米|斤|杯|口|只|位|名|秒))"
)
_ENTITY_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("<身份>", (
        "985", "211", "清华", "北大", "复旦", "交大", "华工", "华南理工", "专科", "本科",
        "硕士", "博士", "研究生", "大学生", "毕业生", "学生", "学霸", "学渣", "学历",
    )),
    ("<地名>", (
        "上海", "北京", "河北", "河南", "山东", "广东", "南昌", "杭州", "义乌", "南京",
        "深圳", "广州", "成都", "东北", "南方", "北方", "老家", "外地", "一线城市",
    )),
    ("<机构>", (
        "文旅局", "旅游局", "公司", "学校", "大学", "医院", "派出所", "法院", "银行",
        "工地", "工厂", "厂里", "单位", "平台", "税务局", "劳动局",
    )),
    ("<人>", (
        "领导", "老板", "上司", "主管", "甲方", "客户", "面试官", "同事", "房东", "物业",
        "老师", "同学", "朋友", "我妈", "我爸", "我爹", "我娘", "女朋友", "男朋友",
        "女友", "男友", "对象", "前任", "儿子", "女儿", "孩子", "老婆", "老公",
    )),
    ("<钱>", (
        "电费", "房租", "工资", "押金", "罚款", "贷款", "账单", "年终奖", "加班费", "薪",
    )),
    ("<东西>", (
        "冰箱", "空调", "水杯", "泡面", "速冻饺子", "猫", "车", "手机", "电脑",
        "暖风扇", "钥匙", "门", "窗", "呼吸机", "橡皮泥", "垃圾桶", "刮刮乐",
    )),
    ("<平台>", ("抖音", "微博", "知乎", "B站", "b站", "快手", "小红书", "朋友圈", "公众号")),
]
# 骨架里允许保留的「结构性」字符。除此之外的拉丁/数字串基本是噪声。
_NOISE_RE = re.compile(r"[A-Za-z]{3,}|[0-9]{2,}")


def _replace_entities(text: str) -> str:
    out = text
    out = _DIGIT_RE.sub("<数字>", out)
    out = _CN_NUM_WITH_UNIT_RE.sub("<数字>", out)
    for placeholder, words in _ENTITY_GROUPS:
        for word in words:
            out = out.replace(word, placeholder)
    return out


def _hanzi_runs(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]+", text or "")


def _has_verbatim_run(skeleton: str, original: str, max_run: int = 12) -> bool:
    """骨架里是否保留了原句的 ≥12 个连续汉字。这是硬约束，命中即不能出。"""
    orig = "".join(_hanzi_runs(original))
    for run in _hanzi_runs(skeleton):
        if len(run) < max_run:
            continue
        for i in range(0, len(run) - max_run + 1):
            if run[i : i + max_run] in orig:
                return True
    return False


def _abstract_segment(segment: str) -> str | None:
    original = (segment or "").strip()
    if not original:
        return None
    body = re.sub(r"^[《「『“\"'(（]+|[》」』”\"')）]+$", "", original).strip()
    skel = _replace_entities(body)
    skel = re.sub(r"[?？!！。.,，、~～…\s]+", "", skel)
    if not skel or hanzi_count(skel) < 3:
        return None
    if _NOISE_RE.search(skel):
        return None
    if _has_verbatim_run(skel, original):
        return None
    if is_low_info(skel):
        return None
    return skel


def abstract_utterance(text: str, max_chars: int = 22) -> str | None:
    """把一句话抽象成骨架。抽象不出来（会保留整句原话/只剩噪声）就返回 None。

    为什么允许「退到分句」：
        很多 utterance 是两三个观众片段的拼接，整句抽象会残留 ≥12 字原话。
        但骨架要的是「一句话的逻辑形状」，不要求复现整段拼接。所以整句
        抽不出来时，退到其中一个分句再抽：优先占位符多的、其次最长的。
        代价是骨架可能只代表簇里的一截——这比编一句假台词要好。
    """
    original = (text or "").strip()
    if not original:
        return None
    whole = _abstract_segment(original)
    if whole is not None:
        # 有占位符才算抽象过；纯短句（≤12 汉字）本身就可以当骨架。
        if "<" in whole or hanzi_count(whole) <= 12:
            return whole if content_len(whole) <= max_chars else whole[:max_chars] + "…"
    # 退到分句：分句一定短于整段，能天然满足「不出现 >12 字原句」。
    parts = [p for p in re.split(r"[，。！？!?、；：\s]+", original) if p.strip()]
    scored: list[tuple[int, int, str]] = []
    for part in parts:
        seg = _abstract_segment(part)
        if seg is None:
            continue
        if not ("<" in seg or hanzi_count(seg) <= 12):
            continue
        scored.append((seg.count("<"), hanzi_count(seg), seg))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], -x[1]))
    skel = scored[0][2]
    if content_len(skel) > max_chars:
        skel = skel[:max_chars] + "…"
    return skel or None


def _bucket_counts(rows: list) -> dict[int, int]:
    counts: dict[int, int] = {}
    for t, _text in rows:
        key = int(t // BUCKET)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _find_peaks(counts: dict[int, int], duration: float | None, top: int = TOP_PEAKS) -> list[dict]:
    """同一视频内部的 1 秒桶峰。先取局部极大值，再按高度贪心、保证间隔。"""
    if not counts:
        return []
    candidates = []
    for key in sorted(counts):
        n = counts[key]
        if n < 3:
            continue
        neighbors = [counts.get(k, 0) for k in range(key - 2, key + 3) if k != key]
        if neighbors and n < max(neighbors):
            continue
        candidates.append({"t": round(key * BUCKET, 2), "n": n})
    candidates.sort(key=lambda x: -x["n"])
    picked: list[dict] = []
    for cand in candidates:
        if all(abs(cand["t"] - p["t"]) >= PEAK_MIN_SEPARATION for p in picked):
            picked.append(cand)
        if len(picked) >= top:
            break
    # 有些视频是整段平台，不是尖峰；局部极大值不够 top 个时用最高桶补齐。
    if len(picked) < top:
        for key in sorted(counts, key=lambda k: -counts[k]):
            t = round(key * BUCKET, 2)
            if counts[key] < 3 or any(abs(t - p["t"]) < PEAK_MIN_SEPARATION for p in picked):
                continue
            picked.append({"t": t, "n": counts[key]})
            if len(picked) >= top:
                break
    picked.sort(key=lambda x: -x["n"])
    for i, p in enumerate(picked, 1):
        p["rank"] = i
    return picked


def _local_attention(counts: dict[int, int], t_start: float, t_end: float) -> int:
    """一条 utterance 覆盖区间内的最高 1 秒桶计数。"""
    lo = int(t_start // BUCKET)
    hi = int(t_end // BUCKET) + 1
    return max((counts.get(k, 0) for k in range(lo, hi + 1)), default=0)


def _overlaps_peak(peaks: list[dict], t_start: float, t_end: float) -> dict | None:
    for peak in peaks:
        if t_end + WINDOW >= peak["t"] >= t_start - WINDOW:
            return peak
    return None


def build_attention(utterances: list[dict], timed: dict | None = None) -> dict:
    """返回 {peaks, skeletons, stats}。peaks 供报告用，skeletons 进 texture.json。"""
    timed = timed or load_timed_danmaku()
    peaks: list[dict] = []
    skeletons: list[dict] = []
    utterances_near_peak = 0
    for bv, item in timed.items():
        rows = item["rows"]
        counts = _bucket_counts(rows)
        video_peaks = _find_peaks(counts, item.get("duration"))
        for peak in video_peaks:
            peaks.append(
                {
                    "bvid": bv,
                    "title": item.get("title") or "",
                    "t": peak["t"],
                    "n": peak["n"],
                    "rank": peak["rank"],
                }
            )
        # 每条 dictation utterance 都量一下它落在多高的注意力桶里。
        # 只保留真正落在 top5 峰附近的：不在峰附近的句子不是「注意力最高的话」，
        # 把它们也收进骨架会让「注意力」这个词失去意义。
        for utt in utterances:
            if utt["bvid"] != bv:
                continue
            middle = (utt["t_start"] + utt["t_end"]) / 2
            n = _local_attention(counts, utt["t_start"], utt["t_end"])
            if n <= 0:
                continue
            peak = _overlaps_peak(video_peaks, utt["t_start"], utt["t_end"])
            if not peak:
                continue
            utterances_near_peak += 1
            skel = abstract_utterance(utt["utterance"])
            if not skel:
                continue
            why = "该视频 1 秒桶第 %d 峰（%d 条）附近" % (peak["rank"], peak["n"])
            skeletons.append(
                {
                    "skeleton": skel,
                    "n": peak["n"],
                    "video": bv,
                    "title": item.get("title") or "",
                    "t": round(middle, 1),
                    "why": why,
                    "seed": utt["utterance"],
                    "mode": utt.get("mode", "strict"),
                    "members": utt.get("n_members", 2),
                }
            )
    # 同一个骨架在不同视频重复出现时合并 n（取最高那次），避免同形状刷榜。
    merged: dict[str, dict] = {}
    for row in skeletons:
        key = row["skeleton"]
        if key not in merged or row["n"] > merged[key]["n"]:
            merged[key] = row
    skeletons = sorted(merged.values(), key=lambda r: -r["n"])[:MAX_SKELETONS]
    stats = {
        "videos": len(timed),
        "peaks": len(peaks),
        "utterances_total": len(utterances),
        "utterances_near_peak": utterances_near_peak,
        "skeletons": len(skeletons),
        "truncated_videos": sum(1 for v in timed.values() if len(v["rows"]) == 1200),
    }
    return {"peaks": peaks, "skeletons": skeletons, "stats": stats}
