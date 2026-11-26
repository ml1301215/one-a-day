# -*- coding: utf-8 -*-
"""听写（dictation）：从弹幕时间轴反推「观众在替他说的话」。

为什么用这个方法：
    这个账号没有中文字幕。观众在看视频时是**边听边打字**，同一段话会被
    不同的人打成不同片段，落在几秒之内。把这些片段按时间聚簇，拼出来的
    不是「观众怎么评价他」，而是「他在说什么」——这是本项目目前离他本人
    最近的一层材料。

为什么规则要这么保守：
    弹幕里同时存在大量「哈哈哈」「？？？」和独立吐槽。聚簇最大的风险不是
    漏，是**把两个不相干的人的话拼成一句他从没说过的话**。所以每条边都要
    过四道硬规则（起头/结尾/长度/情绪）外加一道「语义能接上」的弱规则；
    宁可只剩几百条，也不要几万条假台词。

产出怎么读：
    一条 utterance 是「多个观众在 2.5 秒内接力打出的一段话」的重建，
    **不是任何一个人的原话**，也不是一句逐字稿。它的价值在形状（多短、
    多碎、落在什么词上），不在内容归属。
"""

from __future__ import annotations

import random

from .corpus import clean_danmaku_rows, content_len, hanzi_count, is_low_info, load_timed_danmaku

# 相邻片段的最大时间差。2.5 秒是「同一句话还没说完」的经验上限：
# 再长，观众更可能是在回应上一个段子，而不是接下一句。
GAP_MAX = 2.5
# 一簇的总时长上限。超过这个长度，更可能是连续几条独立反应被串起来了。
SPAN_MAX = 15.0
# 一簇最多几个片段。超过 5 个，人工抽检里几乎全是拼贴。
MAX_MEMBERS = 5
# 参与聚簇的片段至少 4 个字（任务给的硬规则）。
MIN_UNIT_CHARS = 4
# 拼出的 utterance 至少 6 个可视字符，否则没有统计意义。
MIN_UTTERANCE_CHARS = 6

_PUNCT = set("，。！？、；：,.!?;:")
# 「后一句不以语气词起头」：这些字开头基本是反应，不是接话。
_FILLER_START = set("啊哦嗯唉诶哎欸哈嘿喂呀哇嘛呢吧啦咯噢喔呃额唔嘻呵")
_SENT_END = set("。！？!?…~～")
# 评价/反应类起头。它们看着像「接话」，实际是对视频的即时反应。
_REACTION_PREFIXES = (
    "没毛病", "此言", "妈呀", "好家", "笑死", "哈哈", "牛逼", "牛啊", "绝了", "离谱",
    "逆天", "抽象", "难绷", "绷不住", "还真是", "确实", "正确", "太真实", "真实",
    "省流", "前排", "打卡", "关注", "投币", "收藏", "前面的", "我知道", "看懂了",
    "破防", "泪目", "awsl", "妙啊", "好活", "神了", "草", "卧槽", "我靠", "我去",
    "我嘞", "我勒", "真的", "不是说", "话说", "建议", "这里", "这波", "这期",
    "这段", "这句", "这词", "这歌", "这视频", "这个视频", "建议", "up", "UP",
    "好耶", "不是吧", "第一次", "打卡", "已三连", "三连",
)

# 短小的独立评价：以这些语气收尾且短，基本是在评价刚才那句，不是接话。
_EVAL_END = set("了啊呀呢吧哦嘛")
_STOP_CHARS = set("的了是在有我把被给跟和就还也都又再才很太不没要会能想说知道个得地上里着过完到成变去来对为让比像叫做当算这那你他她它们一")

# 强反应词。出现即判「这条是在评价视频」，不是替他说的话。
# 这份词表是从抽样错误里长出来的：第一版只过滤了纯情绪，「我笑死了」
# 「难绷」「好家伙」仍然会被当成接话拼进去，于是三句话变成一段弹幕串。
_REACTION_TOKENS = (
    "哈哈", "hhh", "HHH", "笑死", "笑不活", "绷不住", "难绷", "破防", "泪目", "我靠",
    "卧槽", "我去", "我嘞", "我勒", "好家伙", "离谱", "逆天", "抽象", "绝了", "牛逼",
    "tql", "awsl", "乐死", "太草", "草", "典", "急", "神", "牛", "妙", "真实", "确实",
    "正确的", "省流", "前排", "打卡", "关注", "投币", "收藏", "三连", "doge", "狗头",
    "bushi", "哭", "呜呜", "笑", "弹幕", "视频", "这期", "这段", "这波", "这词",
    "这句", "这句话", "这歌", "标题", "封面", "进度条", "评论", "楼主", "楼上",
    "楼下", "兄弟", "哥们", "家人们", "笑", "乐", "好活", "多来点", "再来点",
    # 第二版补充：这些词是评价，不是叙述。第一版把它们放进聚簇后，
    # 抽检里出现大量「两个观众互相夸视频」被拼成一段的假台词。
    "可爱", "好看", "好听", "好帅", "好笑", "好玩", "好棒", "太棒", "喜欢", "爱了",
    "心疼", "萌", "甜", "尴尬", "感动", "温暖", "治愈", "优秀", "厉害", "精彩",
    "希望", "祝福", "加油", "谢谢", "感谢", "抱歉", "对不起", "救命", "妈耶",
    "我的妈", "天哪", "天啊", "求求", "拜托", "支持", "同意", "反对", "点赞",
    "看完", "看下去", "快进", "倍速", "时长", "这集", "好听", "真不错", "不错",
)

# 观众讲自己经历的起头。这类句子主语是观众，不是他；留在聚簇里会把
# 「我家猫也跑过」拼成「他说他家猫也跑过」。
_AUDIENCE_SELF_PREFIXES = (
    "我也", "我家", "我的猫", "我朋友", "我同学", "我高考", "我妈", "我爸",
    "我女朋友", "我男朋友", "我前", "我室友", "我同事", "我领导", "我老板",
    "我买了", "我租", "我上回", "我上次", "我去年", "我以前", "我以为",
    "我发现", "我刷", "我看完", "我丢", "我弟", "我哥", "我姐", "我妹",
    "我儿", "我女", "我对象", "我相亲", "我面试", "我辞职",
)


def _strip_tail(text: str) -> str:
    """去掉句末的引号/括号装饰，只看真正的最后一个内容字。"""
    t = (text or "").strip()
    while t and t[-1] in "”’」』）)】》>\"'":
        t = t[:-1].rstrip()
    return t


def _is_sentence_end(text: str) -> bool:
    t = _strip_tail(text)
    return bool(t) and t[-1] in _SENT_END


def _starts_badly(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if t[0] in _PUNCT or t[0] in _FILLER_START:
        return True
    for prefix in _REACTION_PREFIXES:
        if t.startswith(prefix):
            return True
    return False


def _shared_content_chars(a: str, b: str) -> int:
    sa = {c for c in a if c not in _STOP_CHARS and "一" <= c}
    sb = {c for c in b if c not in _STOP_CHARS and "一" <= c}
    return len(sa & sb)


def _is_reaction_like(text: str) -> bool:
    """这条弹幕像不像「对视频的即时反应」。

    为什么要单独一个判据而不是只过滤纯情绪：
        第一版抽样里最常见的错误是「A、B 两条独立吐槽被拼成一句」，
        而 B 往往以「我笑死了」「难绷」「确实」这类反应词开头。
        只要有一个片段是反应，拼出来的就不是他说的话。
    """
    t = text or ""
    if any(token in t for token in _REACTION_TOKENS):
        return True
    for prefix in _REACTION_PREFIXES:
        if t.startswith(prefix):
            return True
    for prefix in _AUDIENCE_SELF_PREFIXES:
        if t.startswith(prefix):
            return True
    if content_len(t) <= 10 and _strip_tail(t)[-1:] in _EVAL_END:
        return True
    return False


def can_continue(prev: str, nxt: str) -> tuple[bool, str]:
    """判断两条相邻弹幕能不能接成一句话。返回 (能/不能, 不能的原因)。

    规则顺序是有意排的：先排掉「明显是反应」的，再排掉「明显是完整句」的，
    最后要求一条**强**语义桥。四道硬规则之外再加一道强桥，是为了压住
    「两个不相干的人刚好在同一秒说话」这一类假阳性——这是本方法最大的错误源。

    和任务书给的四条规则相比，这里多做了三件事，都是抽样逼出来的：
      1. 片段长度提到 5 字（4 字里大部分是「没毛病」「确实」这类反应）；
      2. 增加反应词/反应句式过滤（否则观众吐槽会被拼成他的发言）；
      3. 语义桥只认三种强证据：共享 ≥2 个实词、前句带半句标点、
         后句以「所以/那/这/就」起头且至少共享 1 个实词。
    """
    prev_t = prev.strip()
    nxt_t = nxt.strip()
    if content_len(prev_t) < 5 or content_len(nxt_t) < 5:
        return False, "长度<5"
    if is_low_info(nxt_t) or is_low_info(prev_t):
        return False, "纯情绪"
    if _starts_badly(nxt_t):
        return False, "后句起头是标点/语气词/反应"
    if _is_sentence_end(prev_t):
        return False, "前句已句末收尾"
    if _is_reaction_like(prev_t) or _is_reaction_like(nxt_t):
        return False, "反应片段"
    # 强语义桥：三条里至少一条硬证据。
    shared = _shared_content_chars(prev_t, nxt_t)
    if shared >= 2:
        return True, "共享实词"
    if _strip_tail(prev_t)[-1:] in ("，", "、", "；", "：") and nxt_t[0] not in _SENT_END:
        return True, "前句带半句标点"
    if shared >= 1 and nxt_t.startswith(("所以", "那", "这", "就", "是", "不", "没", "我")):
        return True, "起头连接+共享实词"
    return False, "无语义桥"


def can_continue_relay(prev: str, nxt: str, context: list[str] | None = None) -> tuple[bool, str]:
    """接力问句模式：允许前一句以问号收尾。

    为什么留一个例外：
        任务书给的基础规则要求「前一句不以句末标点结尾」，这条在防止
        「把两个完整句拼起来」上是对的。但它会把 docs §6.8.3 记录的
        决定性证据排除掉——那批观众接力弹幕（「所以南昌就是南凉？」
        「所以我是山田凉」）每一条都自带问号，而它们恰恰是「边听边打、
        替他把推理往下接」的最清楚样本。

        所以这里只放一个窄口：前句以 ？ 收尾时，后句必须以
        「所以/那/就是/不是/是/这/不/我」起头（或者与簇内已有片段共享
        ≥3 个实词）、且与簇内**任一片段**共享 ≥1 个实词、不是反应句。
        满足时，问号不视为「话题结束」，而视为「他刚说完一截，观众在接下一截」。

        `context` 传的是当前簇里已有的文本：接力是多人对同一段话的转写，
        共享词可能出现在更早的片段上，只跟紧邻上一句比会漏掉中间插话。
    """
    prev_t = prev.strip()
    nxt_t = nxt.strip()
    if content_len(prev_t) < 5 or content_len(nxt_t) < 5:
        return False, "长度<5"
    if is_low_info(nxt_t) or is_low_info(prev_t):
        return False, "纯情绪"
    if _starts_badly(nxt_t):
        return False, "起头是标点/语气词/反应"
    if _is_reaction_like(prev_t) or _is_reaction_like(nxt_t):
        return False, "反应片段"
    tail = _strip_tail(prev_t)[-1:]
    if tail not in ("？", "?"):
        return False, "前句不是问号"
    shared = max(_shared_content_chars(t, nxt_t) for t in (context or [prev_t]))
    if shared < 1:
        return False, "与簇内无共享实词"
    relay_start = nxt_t.startswith(("所以", "那", "就是", "不是", "是", "这", "不", "我"))
    if not relay_start and shared < 3:
        return False, "后句不是接力起头"
    return True, "接力"


def _finalize(members: list[dict], mode: str = "strict") -> dict | None:
    """把一簇成员拼成 utterance。不足条件返回 None。"""
    if len(members) < 2:
        return None
    t_start = members[0]["t"]
    t_end = members[-1]["t"]
    # 同一秒的几条几乎不可能是「接力」，更可能是同时刷屏。
    if t_end - t_start < 0.2:
        return None
    # 不人为补标点：补出来的「，」会直接污染 punctuation 和 clause_chars 的统计。
    utterance = "".join(m["text"].strip() for m in members)
    if content_len(utterance) < MIN_UTTERANCE_CHARS:
        return None
    if content_len(utterance) > 80:
        return None
    if not any(content_len(m["text"]) >= 6 for m in members):
        return None
    return {
        "bvid": "",
        "t_start": round(t_start, 2),
        "t_end": round(t_end, 2),
        "utterance": utterance,
        "n_members": len(members),
        "mode": mode,
        "members": [{"t": round(m["t"], 2), "text": m["text"]} for m in members],
    }


def cluster_video(rows: list[dict]) -> tuple[list[dict], dict]:
    """单支视频内聚簇。rows 必须已按 t 排序且已清洗。

    先试严格规则，再试接力规则；两条都不成立就收口重开。簇的 mode
    记录它是怎么拼出来的，报告里的质量抽检要按 mode 分开看。
    """
    stats = {"rows": len(rows), "groups": 0, "clusters": 0, "relay_clusters": 0, "rejected": 0, "pairs_ok": 0, "pairs_total": 0}
    clusters: list[dict] = []
    current: list[dict] = []
    current_mode = "strict"

    def _flush() -> None:
        nonlocal current, current_mode
        if len(current) >= 2:
            stats["groups"] += 1
        item = _finalize(current, current_mode)
        if item:
            clusters.append(item)
        else:
            stats["rejected"] += 1
        current = []
        current_mode = "strict"

    for row in rows:
        if not current:
            current = [row]
            current_mode = "strict"
            continue
        prev = current[-1]
        gap = row["t"] - prev["t"]
        stats["pairs_total"] += 1
        span_ok = (row["t"] - current[0]["t"]) <= SPAN_MAX and len(current) < MAX_MEMBERS
        ok, _reason = can_continue(prev["text"], row["text"])
        mode = "strict"
        if not ok:
            ok, _reason = can_continue_relay(prev["text"], row["text"], [m["text"] for m in current])
            mode = "relay"
        if gap <= GAP_MAX and span_ok and ok:
            stats["pairs_ok"] += 1
            current.append(row)
            if mode == "relay":
                current_mode = "relay"
            continue
        _flush()
        current = [row]
        current_mode = "strict"
    _flush()
    for item in clusters:
        if item.get("mode") == "relay":
            stats["relay_clusters"] += 1
    stats["clusters"] = len(clusters)
    return clusters, stats


def build_dataset(*, dedupe: bool = True, sample_n: int = 0, seed: int = 20261126) -> tuple[list[dict], dict]:
    """全量聚簇。返回 (utterances, 总计数)。

    总计数里每个阶段都有 n：原始行、清洗后行、成簇、成 utterance、
    被丢掉的簇。报告和 texture.sources 都从这里取数。
    """
    timed = load_timed_danmaku()
    all_rows = 0
    clean_rows = 0
    per_video: dict[str, dict] = {}
    utterances: list[dict] = []
    clean_stats_total = {"rows": 0, "drop_invalid": 0, "drop_low_info": 0, "drop_short": 0, "drop_duplicate": 0, "kept": 0}
    pair_total = pair_ok = relay_total = groups_total = 0
    for bv, item in timed.items():
        rows = item["rows"]
        cleaned, st = clean_danmaku_rows(rows, dedupe=dedupe, min_chars=MIN_UNIT_CHARS - 2)
        for k in clean_stats_total:
            clean_stats_total[k] += st.get(k, 0)
        clusters, cst = cluster_video(cleaned)
        for utt in clusters:
            utt["bvid"] = bv
        utterances.extend(clusters)
        per_video[bv] = {"title": item["title"], "clean": st, "cluster": cst}
        all_rows += len(rows)
        clean_rows += len(cleaned)
        pair_total += cst["pairs_total"]
        pair_ok += cst["pairs_ok"]
        relay_total += cst["relay_clusters"]
        groups_total += cst["groups"]

    stats = {
        "videos": len(timed),
        "raw_rows": all_rows,
        "clean_rows": clean_rows,
        "clean": clean_stats_total,
        "per_video": per_video,
        "clusters": groups_total,
        "utterances": len({u["utterance"] for u in utterances}),
        "relay_clusters": relay_total,
        "pair_total": pair_total,
        "pair_ok": pair_ok,
        "sample": [],
    }
    if sample_n:
        rng = random.Random(seed)
        picks = rng.sample(utterances, min(sample_n, len(utterances)))
        stats["sample"] = picks
    return utterances, stats


def quality_sample(utterances: list[dict], n: int = 10, seed: int = 20261126) -> list[dict]:
    """随机抽 n 条给人看。抽检是这套方法唯一的质量证据。"""
    rng = random.Random(seed)
    return rng.sample(utterances, min(n, len(utterances)))
