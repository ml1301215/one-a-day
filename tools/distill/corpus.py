# -*- coding: utf-8 -*-
"""语料加载与清洗：把 `.work/` 里五类材料读成统一形状，并做可复核的清洗。

为什么单独一层：
    「清洗」是这条流水线里唯一会影响所有下游数字的步骤。把它写死在
    dictation / texture 里，任何一个过滤规则改动都会变成两处不同实现，
    报告里的 n 就对不上了。所以这里每一步都返回计数，报告直接引用，
    而不用「大概去了很多」这种说法。

硬边界（必须和产物一起交付）：
    - `.work/` 是只读语料，本模块只读不写。
    - 弹幕是**观众**打的字，不是他的逐字稿。它是二手材料，清洗只能去掉
      噪声，去不掉「二手」这个性质。
    - 20 个视频里有 11 个的弹幕行数正好 1200，是 B 站 XML 接口的单视频
      上限（数据缺陷，不是全量）。这 11 支视频的 1 秒密度是**抽样密度**，
      不能与其他视频横向比较，只能在同一支视频内部看峰。
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

# tools/distill/corpus.py -> 仓库根目录
ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".work"

# 汉字。统计「字数」时统一用这个，不用 len()——len 会把标点、数字、英文字母
# 一起算进去，而人格包的长度语义是「汉字数」。
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
HANZI_RE = CJK_RE

# 一条弹幕/评论里要脱敏的东西。为什么在语料层做而不是展示层做：
# 下游要按文本去重，@某人 和 bv 号会让「同一条话」看起来不一样；
# 而链接会把统计带偏（一条 URL 的字数远大于它承载的信息）。
_AT_RE = re.compile(r"@[^\s@，。！？、,:;；]{1,20}")
_URL_RE = re.compile(r"https?://[^\s，。！？）)】]+")
_BV_RE = re.compile(r"[Bb][Vv][0-9A-Za-z]{10}")
_QQ_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")  # QQ/手机号；985、211 这种短数字保留
_EMOJI_CODE_RE = re.compile(r"\[[^\[\]]{1,8}\]")  # B 站弹幕表情 [捂脸]
_SPACE_RE = re.compile(r"[ \t\u3000]+")

# 纯情绪/复读：这类弹幕对「他说了什么」零信息，但对「他哪句炸了」信息量很大。
# 所以清洗时丢掉，注意力统计时仍用原始行。
_EMOTION_WORDS = (
    "哈哈哈|哈哈|呵呵|嘿嘿|嘻嘻|呜呜|嘤嘤|awsl|啊这|啊|呀|哦|噢|喔|嗯|额|呃|"
    "诶|唉|哎|草|卧槽|我靠|我去|我嘞|我勒|笑死|笑死了|笑死我了|绷不住|难绷|"
    "破防|泪目|离谱|逆天|抽象|好家伙|绝了|牛逼|牛|666|6|神|妙|好活|太强了|"
    "可以|真行|麻了|真实|太真实了|是这样的|确实|正确的|你说的对|前面的|省流|"
    "前排|打卡|三连|已投币|收藏了|关注了|学习了|懂了|知道了|好|对|是|牛啊|"
    "awsl|泪目了|哈哈哈了|笑|乐|乐了|急了|急|典|太典了|多来点|再来点"
)
_EMOTION_RE = re.compile(r"^(?:(?:%s))+$" % _EMOTION_WORDS)
_PUNCT_ONLY_RE = re.compile(r"^[\s?？!！。.,，、~～…\-—:：;；'\"“”‘’()（）\[\]【】]+$")
# 连续同一个字，如「哈哈哈哈哈哈」「？？？？？？」
_REPEAT_CHAR_RE = re.compile(r"^(.)\1{2,}$")


def _load_json(name: str, default):
    path = WORK / name
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def hanzi_count(text: str) -> int:
    return len(CJK_RE.findall(text or ""))


def content_len(text: str) -> int:
    """去掉空白后的可视长度。判断「≥4 字」用这个，不要求全是汉字。"""
    return len(re.sub(r"\s", "", text or ""))


def load_timed_danmaku() -> dict:
    """{bvid: {title, duration, rows: [[秒, 文本], ...]}}，按原始顺序。"""
    raw = _load_json("danmaku_timed.json", {})
    out = {}
    for bv, item in (raw or {}).items():
        if not isinstance(item, dict):
            continue
        rows = []
        for row in item.get("rows") or []:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            try:
                t = float(row[0])
            except (TypeError, ValueError):
                continue
            text = str(row[1] or "").strip()
            if text:
                rows.append([t, text])
        rows.sort(key=lambda r: r[0])
        out[bv] = {
            "title": str(item.get("title") or ""),
            "duration": item.get("duration"),
            "rows": rows,
        }
    return out


def load_danmaku_text() -> dict:
    """{bvid: {title, danmaku: [文本]}}，只有文本、无时间轴。"""
    raw = _load_json("danmaku.json", {})
    out = {}
    for bv, item in (raw or {}).items():
        if not isinstance(item, dict):
            continue
        out[bv] = {
            "title": str(item.get("title") or ""),
            "danmaku": [str(x) for x in (item.get("danmaku") or []) if str(x).strip()],
        }
    return out


def load_comments() -> list[dict]:
    raw = _load_json("comments_full.json", [])
    out = []
    for c in raw or []:
        if not isinstance(c, dict):
            continue
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "bv": str(c.get("bv") or ""),
                "layer": c.get("layer"),
                "mid": str(c.get("mid") or ""),
                "uname": str(c.get("uname") or ""),
                "t": c.get("t"),
                "like": c.get("like") or 0,
                "text": text,
                "rcount": c.get("rcount") or 0,
            }
        )
    return out


def load_videos() -> dict:
    raw = _load_json("videos.json", {})
    return raw if isinstance(raw, dict) else {}


def load_bvids() -> dict:
    raw = _load_json("bvids.json", {})
    return raw if isinstance(raw, dict) else {}


def load_transcripts() -> dict:
    """3 份英文机器翻译稿。

    只用来量结构（时长、句数、词数、连接词/第一人称密度），
    不参与中文措辞统计。为什么不做回译：翻译层已经把断句、口误、口癖
    全部抹平，回译出来的中文是我们自己写的，只是套了一层「有出处」的壳。
    """
    out = {}
    folder = WORK / "transcripts"
    if not folder.is_dir():
        return out
    line_re = re.compile(r"^\[(\d+):(\d+)\]\s*\((\d+)\)\s*(.*)$")
    for path in sorted(folder.glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        head = text.splitlines()[0] if text.splitlines() else ""
        m = re.search(r"dur=(\d+)s", head)
        duration = int(m.group(1)) if m else 0
        m = re.search(r"lan=([\w-]+)", head)
        lan = m.group(1) if m else ""
        lines = []
        for raw_line in text.splitlines():
            mm = line_re.match(raw_line.strip())
            if not mm:
                continue
            minute, second, n, content = mm.groups()
            lines.append(
                {
                    "t": int(minute) * 60 + int(second),
                    "n": int(n),
                    "text": content.strip(),
                }
            )
        words = sum(len(re.findall(r"[A-Za-z][A-Za-z'-]*", x["text"])) for x in lines)
        chars = sum(len(x["text"]) for x in lines)
        out[path.stem] = {
            "bvid": path.stem,
            "duration": duration,
            "lan": lan,
            "lines": lines,
            "sentences": len(lines),
            "words": words,
            "chars": chars,
        }
    return out


def load_titles() -> tuple[list[str], list[str]]:
    """标题层：先从空间页解析，失败回落 videos.json。

    返回 (标题列表, 解析说明)。`.work/space.html` 实测是**未水合的 JS 外壳**
    （10KB，只有白屏检测脚本和站点级 meta），里面 0 个投稿标题；
    这是本机语料的一个已知缺口，报告里必须写明。这里保留解析尝试，
    是为了将来换一份渲染后的页面时不用改代码。
    """
    notes: list[str] = []
    titles: list[str] = []
    page = WORK / "space.html"
    if page.is_file():
        h = page.read_text(encoding="utf-8", errors="replace")
        patterns = [
            re.compile(r'class="bili-video-card__info--tit"[^>]*title="([^"]+)"'),
            re.compile(r'title="([^"]{4,60})"[^>]*class="bili-video-card__info--tit"'),
            re.compile(r'"title":"([^"]{4,60})"'),
        ]
        for pat in patterns:
            for t in pat.findall(h):
                t = html.unescape(t).strip()
                if t and t not in titles:
                    titles.append(t)
            if titles:
                break
        notes.append("space.html 解析到 %d 个标题" % len(titles))
    else:
        notes.append("space.html 不存在")

    if not titles:
        videos = load_videos()
        bvids = load_bvids()
        for bv, meta in videos.items():
            title = str(meta.get("title") or "").strip()
            if not title and bv in bvids:
                title = str(bvids[bv][0])
            if title and title not in titles:
                titles.append(title)
        notes.append("空间页无标题，回落 videos.json：%d 个标题" % len(titles))
    return titles, notes


def desensitize(text: str) -> str:
    """去掉 @某人 / bv 号 / 链接 / 长数字 / 弹幕表情。"""
    t = html.unescape(text or "")
    t = _URL_RE.sub("", t)
    t = _AT_RE.sub("", t)
    t = _BV_RE.sub("", t)
    t = _EMOJI_CODE_RE.sub("", t)
    t = _QQ_RE.sub("", t)
    t = _SPACE_RE.sub(" ", t)
    return t.strip()


def is_low_info(text: str) -> bool:
    """纯情绪 / 纯标点 / 单字复读。这类弹幕没有内容信息。"""
    core = re.sub(r"[\s?？!！。.,，、~～…\-—:：;；'\"“”‘’()（）\[\]【】]", "", text or "")
    if not core:
        return True
    if _PUNCT_ONLY_RE.match(text or ""):
        return True
    if _REPEAT_CHAR_RE.match(core):
        return True
    return bool(_EMOTION_RE.match(core))


def clean_danmaku_rows(
    rows: list,
    *,
    dedupe: bool = True,
    min_chars: int = 2,
) -> tuple[list[dict], dict]:
    """清洗单支视频的带时间轴弹幕，返回 (清洗后行, 计数)。

    计数逐项返回，因为报告要用它说明「19553 条里真正能进模型的是多少」。
    去重按「整支视频内文本相同即算重复」：弹幕有一类刷屏是同一条复制
    数百遍（「很简单」835 条），留一条就够；如果按时间窗去重，
    高峰期一个复制品都去不掉，聚簇会被同一条文本污染。
    """
    stats = {
        "rows": len(rows),
        "drop_invalid": 0,
        "drop_low_info": 0,
        "drop_short": 0,
        "drop_duplicate": 0,
        "kept": 0,
    }
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        t, raw = row[0], str(row[1])
        text = desensitize(raw)
        if not text:
            stats["drop_invalid"] += 1
            continue
        if is_low_info(text):
            stats["drop_low_info"] += 1
            continue
        if content_len(text) < min_chars:
            stats["drop_short"] += 1
            continue
        key = re.sub(r"\s", "", text)
        if dedupe and key in seen:
            stats["drop_duplicate"] += 1
            continue
        seen.add(key)
        out.append({"t": float(t), "text": text, "raw": raw})
    stats["kept"] = len(out)
    return out, stats


def clean_comments(comments: list[dict]) -> tuple[list[dict], dict]:
    """评论清洗：脱敏 + 去纯情绪，不去重（评论的字数分布本身有用）。"""
    stats = {"rows": len(comments), "drop_low_info": 0, "drop_short": 0, "kept": 0}
    out = []
    for c in comments:
        text = desensitize(c["text"])
        if is_low_info(text):
            stats["drop_low_info"] += 1
            continue
        if content_len(text) < 2:
            stats["drop_short"] += 1
            continue
        item = dict(c)
        item["text"] = text
        out.append(item)
    stats["kept"] = len(out)
    return out, stats
