# -*- coding: utf-8 -*-
"""自检：对 texture.json 逐字段做 schema 断言，对 shotbank.jsonl 做硬校验。

为什么要有这一层：
    后端是「字段缺失就回落、字段类型不对就静默跳过」的容错写法，所以
    一个 0.35 写成 "35%" 的 texture.json 能一路跑到线上，只是打分器悄悄失灵。
    这里用断言把「能读」升级成「读到的意思是对的」。

用法：
    python tools/distill/validate.py
    退出码 0 = 全部通过；1 = 有失败项。
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

# 与 run.py 同理：PowerShell 下打印中文校验结果不能依赖控制台默认编码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distill import corpus  # noqa: E402

PERSONA_DIR = corpus.ROOT / "packages" / "persona" / "brufen"
TEXTURE_PATH = PERSONA_DIR / "texture.json"
SHOTBANK_PATH = PERSONA_DIR / "shotbank.jsonl"
PERSONA_PATH = PERSONA_DIR / "persona.yaml"

TOP_KEYS = (
    "version", "generated_at", "sources", "bands", "punctuation",
    "conjunctions", "self", "openers", "tics", "skeletons", "themes",
    "title_devices", "limits", "his_replies",
)
SOURCE_KEYS = (
    "videos", "danmaku_total", "danmaku_truncated_videos", "comments",
    "titles", "transcripts_en", "dictation_clusters", "dictation_utterances",
    "method_notes",
)
BAND_KEYS = {
    "reply_chars": ("p10", "p25", "p50", "p75", "p90"),
    "sentences": ("p10", "p50", "p90"),
    "clause_chars": ("p10", "p50", "p90"),
}
PUNCT_KEYS = ("period_ending_ratio", "question_ratio", "excl_ratio", "ellipsis_ratio")
CONJ_KEYS = ("所以", "但是", "然后", "其实", "就是")
MAX_ARRAY = 40

EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U0001F1E6-\U0001F1FF" "\u2600-\u27BF" "\uFE0F" "\u2B00-\u2BFF" "]"
)
MARKDOWN_RE = re.compile(r"(\*\*|^#{1,6}\s|```|^\s*[-*+]\s|^\s*\d+\.\s)", re.MULTILINE)
LIST_RE = re.compile(r"^\s*(\d+[.、)]|[-*+•])\s", re.MULTILINE)
SENT_SPLIT = re.compile(r"[。！？!?…\n]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
SELF_OPENER_RE = re.compile(r"^(我上回|我去年|我以前|我那次|我上次|我小时候|我上个月|我当年|我曾经|我那会儿)")
# 解释/半解释自己的梗。前七个来自后端 scorer 的 _EXPLAIN_PUN_RE，后一组是抽检补充。
PUN_EXPLAIN_RE = re.compile(
    r"(一个音|同音字?|念起来像|读起来像|谐音是|这词(以前)?写作|押韵"
    r"|只差|就差|差一个|差一把|换个字|换一个字|拆开|拆成|念作|读作|音同|音近"
    r"|同一个字|署的是|属的是|写的是同一个|说的是同一个)"
)
HIS_HIS_KEYS = ("n", "chars", "sentences", "punctuation", "tics", "examples", "relation_to_parent", "limits")
HIS_UNSAFE_RE = re.compile(r"(老二|直男|男同|做爱|炮|裸|胸|屁股|屎|尿|操|他妈|妈的|傻逼|早谢|早泄)")


def _err(errors: list[str], msg: str) -> None:
    errors.append(msg)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_ratio(v) -> bool:
    return _is_num(v) and 0.0 <= float(v) <= 1.0


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_texture(data: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["根节点不是对象"]
    for key in TOP_KEYS:
        if key not in data:
            _err(errors, "缺字段 %s" % key)
    if errors:
        return errors

    if data["version"] != 1:
        _err(errors, "version 必须是 1")
    if not isinstance(data["generated_at"], str) or "T" not in data["generated_at"]:
        _err(errors, "generated_at 必须是 ISO8601 字符串")

    src = data["sources"]
    if not isinstance(src, dict):
        _err(errors, "sources 不是对象")
    else:
        for key in SOURCE_KEYS:
            if key not in src:
                _err(errors, "sources 缺字段 %s" % key)
        for key in SOURCE_KEYS[:-1]:
            if key in src and not _is_int(src[key]):
                _err(errors, "sources.%s 必须是整数" % key)
        if "method_notes" in src and not (
            isinstance(src["method_notes"], list)
            and src["method_notes"]
            and all(isinstance(x, str) and x for x in src["method_notes"])
        ):
            _err(errors, "sources.method_notes 必须是非空字符串数组")

    bands = data["bands"]
    if not isinstance(bands, dict):
        _err(errors, "bands 不是对象")
    else:
        for name, keys in BAND_KEYS.items():
            band = bands.get(name)
            if not isinstance(band, dict):
                _err(errors, "bands.%s 缺失或不是对象" % name)
                continue
            for key in keys:
                if key not in band:
                    _err(errors, "bands.%s 缺字段 %s" % (name, key))
                elif not _is_int(band[key]):
                    _err(errors, "bands.%s.%s 必须是整数" % (name, key))
                elif band[key] < 0:
                    _err(errors, "bands.%s.%s 不能为负" % (name, key))
            values = [band.get(k, 0) for k in keys]
            if values != sorted(values):
                _err(errors, "bands.%s 的分位必须单调不减：%s" % (name, values))
            if values and values[-1] > 300:
                _err(errors, "bands.%s p90=%s 显然不是口语长度" % (name, values[-1]))

    pun = data["punctuation"]
    if not isinstance(pun, dict):
        _err(errors, "punctuation 不是对象")
    else:
        for key in PUNCT_KEYS:
            if key not in pun:
                _err(errors, "punctuation 缺字段 %s" % key)
            elif not _is_ratio(pun[key]):
                _err(errors, "punctuation.%s 必须是 0–1 小数" % key)

    conj = data["conjunctions"]
    if not isinstance(conj, dict):
        _err(errors, "conjunctions 不是对象")
    else:
        for key in CONJ_KEYS:
            if key not in conj:
                _err(errors, "conjunctions 缺字段 %s" % key)
            elif not _is_num(conj[key]) or float(conj[key]) < 0:
                _err(errors, "conjunctions.%s 必须是 ≥0 的浮点（每句平均次数）" % key)

    selfd = data["self"]
    if not isinstance(selfd, dict):
        _err(errors, "self 不是对象")
    else:
        if not _is_ratio(selfd.get("first_person_ratio")):
            _err(errors, "self.first_person_ratio 必须是 0–1 小数")
        if not _is_num(selfd.get("first_person_per_100")) or selfd.get("first_person_per_100", -1) < 0:
            _err(errors, "self.first_person_per_100 必须是 ≥0 的浮点")

    openers = data["openers"]
    if not isinstance(openers, list):
        _err(errors, "openers 不是数组")
    else:
        if len(openers) > MAX_ARRAY:
            _err(errors, "openers 超过 %d 条" % MAX_ARRAY)
        ns = []
        for item in openers:
            if not isinstance(item, dict) or set(item) != {"text", "n", "share"}:
                _err(errors, "openers 条目字段必须是 text/n/shape: %r" % (item,))
                continue
            if not isinstance(item["text"], str) or not item["text"]:
                _err(errors, "openers.text 必须是字符串")
            if not _is_int(item["n"]) or item["n"] < 0:
                _err(errors, "openers.n 必须是整数")
            if not _is_ratio(item["share"]):
                _err(errors, "openers.share 必须是 0–1 小数")
            ns.append(item.get("n", 0))
        if ns != sorted(ns, reverse=True):
            _err(errors, "openers 必须按 n 降序")

    tics = data["tics"]
    if not isinstance(tics, list):
        _err(errors, "tics 不是数组")
    else:
        if len(tics) > MAX_ARRAY:
            _err(errors, "tics 超过 %d 条" % MAX_ARRAY)
        ns = []
        for item in tics:
            if not isinstance(item, dict) or set(item) != {"word", "n", "scope", "note"}:
                _err(errors, "tics 条目字段必须是 word/n/scope/note: %r" % (item,))
                continue
            if not isinstance(item["word"], str) or not item["word"]:
                _err(errors, "tics.word 必须是字符串")
            if not _is_int(item["n"]) or item["n"] < 0:
                _err(errors, "tics.n 必须是整数")
            if not isinstance(item["scope"], str):
                _err(errors, "tics.scope 必须是字符串")
            if not isinstance(item["note"], str):
                _err(errors, "tics.note 必须是字符串")
            ns.append(item.get("n", 0))
        if ns != sorted(ns, reverse=True):
            _err(errors, "tics 必须按 n 降序")

    skeletons = data["skeletons"]
    if not isinstance(skeletons, list):
        _err(errors, "skeletons 不是数组")
    else:
        if len(skeletons) > MAX_ARRAY:
            _err(errors, "skeletons 超过 %d 条" % MAX_ARRAY)
        ns = []
        for item in skeletons:
            if not isinstance(item, dict) or set(item) != {"skeleton", "n", "video", "t", "why"}:
                _err(errors, "skeletons 条目字段必须是 skeleton/n/video/t/why: %r" % (item,))
                continue
            skel = item["skeleton"]
            if not isinstance(skel, str) or not skel:
                _err(errors, "skeleton 必须是非空字符串")
            elif len(CJK_RE.findall(skel)) > 24:
                _err(errors, "skeleton 太长，不像抽象骨架：%s" % skel)
            if not _is_int(item["n"]) or item["n"] < 0:
                _err(errors, "skeletons.n 必须是整数")
            if not isinstance(item["video"], str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", item["video"]):
                _err(errors, "skeletons.video 必须是 BV 号")
            if not _is_num(item["t"]) or item["t"] < 0:
                _err(errors, "skeletons.t 必须是秒数")
            if not isinstance(item["why"], str) or not item["why"]:
                _err(errors, "skeletons.why 必须是非空字符串")
            ns.append(item.get("n", 0))
        if ns != sorted(ns, reverse=True):
            _err(errors, "skeletons 必须按 n 降序")

    themes = data["themes"]
    if not isinstance(themes, list):
        _err(errors, "themes 不是数组")
    else:
        if len(themes) > MAX_ARRAY:
            _err(errors, "themes 超过 %d 条" % MAX_ARRAY)
        shares = []
        for item in themes:
            if not isinstance(item, dict) or set(item) != {"name", "share", "keywords"}:
                _err(errors, "themes 条目字段必须是 name/share/keywords: %r" % (item,))
                continue
            if not isinstance(item["name"], str) or not item["name"]:
                _err(errors, "themes.name 必须是字符串")
            if not _is_ratio(item["share"]):
                _err(errors, "themes.share 必须是 0–1 小数")
            if not isinstance(item["keywords"], list) or not item["keywords"]:
                _err(errors, "themes.keywords 必须是非空数组")
            shares.append(item.get("share", 0))
        if shares != sorted(shares, reverse=True):
            _err(errors, "themes 必须按 share 降序")

    td = data["title_devices"]
    if not isinstance(td, dict):
        _err(errors, "title_devices 不是对象")
    else:
        for key in ("idiom_rewrite", "contrast_pair", "self_deprecation", "n_titles"):
            if key not in td:
                _err(errors, "title_devices 缺字段 %s" % key)
            elif not _is_int(td[key]) or td[key] < 0:
                _err(errors, "title_devices.%s 必须是 ≥0 整数" % key)
        if "n_titles" in td and "titles" in src and td["n_titles"] != src["titles"]:
            _err(errors, "title_devices.n_titles 必须等于 sources.titles")
        for key in ("idiom_rewrite", "contrast_pair", "self_deprecation"):
            if key in td and "n_titles" in td and td[key] > td["n_titles"]:
                _err(errors, "title_devices.%s 不能超过 n_titles" % key)

    limits = data["limits"]
    if not isinstance(limits, list) or not limits or not all(isinstance(x, str) and x for x in limits):
        _err(errors, "limits 必须是非空字符串数组")

    validate_his_replies(data, errors)
    return errors


def validate_his_replies(data: dict, errors: list[str]) -> None:
    his = data.get("his_replies")
    if not isinstance(his, dict):
        _err(errors, "his_replies 缺失或不是对象")
        return
    for key in HIS_HIS_KEYS:
        if key not in his:
            _err(errors, "his_replies 缺字段 %s" % key)
    if not _is_int(his.get("n")) or his["n"] < 0:
        _err(errors, "his_replies.n 必须是 ≥0 整数")
        return
    chars = his.get("chars") or {}
    for key in ("p10", "p25", "p50", "p75", "p90"):
        if not _is_int(chars.get(key)):
            _err(errors, "his_replies.chars.%s 必须是整数" % key)
    vals = [chars.get(k, 0) for k in ("p10", "p25", "p50", "p75", "p90")]
    if vals != sorted(vals):
        _err(errors, "his_replies.chars 分位必须单调不减")
    sents = his.get("sentences") or {}
    for key in ("p10", "p50", "p90"):
        if not _is_int(sents.get(key)):
            _err(errors, "his_replies.sentences.%s 必须是整数" % key)
    pun = his.get("punctuation") or {}
    for key in ("period", "question", "excl", "ellipsis", "tilde", "no_end_punct"):
        if not _is_ratio(pun.get(key)):
            _err(errors, "his_replies.punctuation.%s 必须是 0–1 小数" % key)
    tics = his.get("tics")
    if not isinstance(tics, list):
        _err(errors, "his_replies.tics 不是数组")
    else:
        if len(tics) > MAX_ARRAY:
            _err(errors, "his_replies.tics 超过 %d 条" % MAX_ARRAY)
        ns = []
        for item in tics:
            if not isinstance(item, dict) or set(item) != {"word", "n", "example"}:
                _err(errors, "his_replies.tics 条目字段必须是 word/n/example")
                continue
            if not _is_int(item.get("n")) or item["n"] < 0:
                _err(errors, "his_replies.tics.n 必须是整数")
            if not isinstance(item.get("word"), str) or not item["word"]:
                _err(errors, "his_replies.tics.word 必须是字符串")
            ex = item.get("example") or ""
            if not isinstance(ex, str) or len(CJK_RE.findall(ex)) > 12:
                _err(errors, "his_replies.tics.example 必须 ≤12 汉字")
            ns.append(item.get("n", 0))
        if ns != sorted(ns, reverse=True):
            _err(errors, "his_replies.tics 必须按 n 降序")
    examples = his.get("examples")
    if not isinstance(examples, list):
        _err(errors, "his_replies.examples 不是数组")
    else:
        if len(examples) > MAX_ARRAY:
            _err(errors, "his_replies.examples 超过 %d 条" % MAX_ARRAY)
        for item in examples:
            if not isinstance(item, dict) or set(item) != {"text", "bv", "t"}:
                _err(errors, "his_replies.examples 条目字段必须是 text/bv/t")
                continue
            text = item.get("text") or ""
            if not isinstance(text, str) or not text or len(CJK_RE.findall(text)) > 12:
                _err(errors, "his_replies.examples.text 必须非空且 ≤12 汉字")
            if HIS_UNSAFE_RE.search(str(text)):
                _err(errors, "his_replies.examples 含不适合进人格包的词：%s" % text)
            if not isinstance(item.get("bv"), str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", item["bv"]):
                _err(errors, "his_replies.examples.bv 必须是 BV 号")
            if not _is_num(item.get("t")) or item["t"] < 0:
                _err(errors, "his_replies.examples.t 必须是时间戳")
    rel = his.get("relation_to_parent")
    if not isinstance(rel, dict) or set(rel) != {"note", "median_ratio"}:
        _err(errors, "his_replies.relation_to_parent 字段必须是 note/median_ratio")
    else:
        if not isinstance(rel.get("note"), str):
            _err(errors, "his_replies.relation_to_parent.note 必须是字符串")
        if not _is_num(rel.get("median_ratio")) or rel["median_ratio"] < 0:
            _err(errors, "his_replies.relation_to_parent.median_ratio 必须是 ≥0 浮点")
    limits = his.get("limits")
    if not isinstance(limits, list) or not limits or not all(isinstance(x, str) and x for x in limits):
        _err(errors, "his_replies.limits 必须是非空字符串数组")
    # 主口径切换校验：可用样本 ≥100 时，bands.reply_chars 必须等于 his_replies.chars。
    if his.get("n", 0) >= 100:
        if (data.get("bands") or {}).get("reply_chars") != his.get("chars"):
            _err(errors, "his_replies.n≥100 时 bands.reply_chars 必须等于 his_replies.chars（主口径已切换）")
    elif his.get("n", 0) > 0:
        notes = " ".join((data.get("sources") or {}).get("method_notes") or [])
        if "his_replies" not in notes:
            _err(errors, "his_replies 样本 <100 未切换主口径时，method_notes 必须写明原因")


def _hanzi(text: str) -> str:
    return "".join(CJK_RE.findall(text or ""))


def _longest_common(a: str, b: str, n: int = 12) -> bool:
    """a 里是否存在长度 n 的连续汉字串出现在 b 里。去重用的 12 字子串。"""
    if len(a) < n or len(b) < n:
        return False
    for i in range(0, len(a) - n + 1):
        if a[i : i + n] in b:
            return True
    return False


def validate_shotbank(path: Path, texture: dict | None = None) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return ["shotbank.jsonl 不存在"]
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            errors.append("第 %d 行不是合法 JSON：%s" % (lineno, exc))
    if errors:
        return errors
    if not rows:
        return ["shotbank.jsonl 为空"]

    required = ("id", "topic", "form", "user", "reply", "level", "chars", "sentences", "has_self_story", "origin", "seed_from")
    ids: set[str] = set()
    replies: list[str] = []          # 只收「生成样本」，用于 12 字去重与自述配额
    all_replies: list[str] = []      # 含种子，用于结构核对
    self_openers = 0
    bands = (texture or {}).get("bands") or {}
    chars_band = bands.get("reply_chars") or {}
    sent_band = bands.get("sentences") or {}
    char_min = max(1, int(math.floor(chars_band.get("p10", 0) * 0.8)))
    char_max = int(math.ceil(chars_band.get("p90", 0) * 1.2))
    sent_p10 = max(1, int(sent_band.get("p10", 1)))
    sent_max = int(sent_band.get("p90", 6)) + 1
    for i, row in enumerate(rows, 1):
        for key in required:
            if key not in row:
                errors.append("第 %d 行缺字段 %s" % (i, key))
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            errors.append("第 %d 行 id 非法" % i)
        elif rid in ids:
            errors.append("id 重复：%s" % rid)
        ids.add(str(rid))
        user = row.get("user") or ""
        reply = row.get("reply") or ""
        if not isinstance(user, str) or not user.strip():
            errors.append("第 %d 行 user 为空" % i)
        if not isinstance(reply, str) or not reply.strip():
            errors.append("第 %d 行 reply 为空" % i)
            continue
        is_seed = str(row.get("origin") or "") == "persona-seed"
        n_char = len(CJK_RE.findall(reply))
        n_sent = len([s for s in SENT_SPLIT.split(reply) if s.strip()])
        if _is_int(row.get("chars")) and row["chars"] != n_char:
            errors.append("第 %d 行 chars=%s 与实算 %d 不符" % (i, row["chars"], n_char))
        if _is_int(row.get("sentences")) and row["sentences"] != n_sent:
            errors.append("第 %d 行 sentences=%s 与实算 %d 不符" % (i, row["sentences"], n_sent))
        if EMOJI_RE.search(reply):
            errors.append("第 %d 行有 emoji" % i)
        if MARKDOWN_RE.search(reply) or LIST_RE.search(reply):
            errors.append("第 %d 行有 Markdown/列表" % i)
        all_replies.append(reply)
        # 种子是输入不是产物：它的长度/句数本来就不在语料带内（L3 危机模板尤其超长），
        # 不能拿生成样本的硬校验去卡它。其余行必须逐条在带内。
        if not is_seed:
            if char_min and char_max and not (char_min <= n_char <= char_max):
                errors.append("第 %d 行字数 %d 不在带内 [%d, %d]" % (i, n_char, char_min, char_max))
            if not (sent_p10 <= n_sent <= sent_max):
                errors.append("第 %d 行句数 %d 不在带内 [%d, %d]" % (i, n_sent, sent_p10, sent_max))
            if SELF_OPENER_RE.match(reply):
                self_openers += 1
            # 词法层硬规则：缺句末标点/空格断句/超长单句/解释梗，都会教坏模型。
            if reply.rstrip()[-1] not in "。！？…":
                errors.append("第 %d 行末尾缺句末标点" % i)
            if " " in reply:
                errors.append("第 %d 行用 ASCII 空格断句" % i)
            for sent in [s for s in SENT_SPLIT.split(reply) if s.strip()]:
                if len(CJK_RE.findall(sent)) > 30:
                    errors.append("第 %d 行有超过 30 汉字的单句" % i)
                    break
            if PUN_EXPLAIN_RE.search(reply):
                errors.append("第 %d 行解释/半解释了自己的梗" % i)
            replies.append(reply)
    for reply in replies:
        for seed in _persona_replies():
            if _longest_common(_hanzi(reply), _hanzi(seed), 12):
                errors.append("与人格包示例存在 12 字重合：%s" % reply[:24])
                break
    for i in range(len(replies)):
        for j in range(i + 1, len(replies)):
            if _longest_common(_hanzi(replies[i]), _hanzi(replies[j]), 12):
                errors.append("示例之间存在 12 字重合：%s / %s" % (replies[i][:16], replies[j][:16]))
                break
    if replies and self_openers / len(replies) > 1 / 3:
        errors.append("「我上回/我去年」类自述开头占比 %d/%d 超过 1/3" % (self_openers, len(replies)))
    if len(rows) < 13:
        errors.append("shotbank 只有 %d 条，少于种子数 13" % len(rows))
    return errors


_PERSONA_REPLIES_CACHE: list[str] | None = None


def _persona_replies() -> list[str]:
    global _PERSONA_REPLIES_CACHE
    if _PERSONA_REPLIES_CACHE is not None:
        return _PERSONA_REPLIES_CACHE
    import yaml

    try:
        raw = yaml.safe_load(PERSONA_PATH.read_text(encoding="utf-8"))
        _PERSONA_REPLIES_CACHE = [str(s.get("reply") or "") for s in (raw.get("few_shots") or [])]
    except Exception:
        _PERSONA_REPLIES_CACHE = []
    return _PERSONA_REPLIES_CACHE


def main() -> int:
    errors: list[str] = []
    if not TEXTURE_PATH.is_file():
        print("✗ texture.json 不存在：%s" % TEXTURE_PATH)
        return 1
    try:
        data = json.loads(TEXTURE_PATH.read_text(encoding="utf-8"))
    except ValueError as exc:
        print("✗ texture.json 不是合法 JSON：%s" % exc)
        return 1
    tex_errors = validate_texture(data)
    errors.extend(tex_errors)
    print("texture.json：%s" % ("✓ 通过全部字段断言" if not tex_errors else "✗ %d 项失败" % len(tex_errors)))
    for e in tex_errors:
        print("  - %s" % e)

    if SHOTBANK_PATH.is_file():
        sb_errors = validate_shotbank(SHOTBANK_PATH, data)
        errors.extend(sb_errors)
        print("shotbank.jsonl：%s" % ("✓ 通过" if not sb_errors else "✗ %d 项失败" % len(sb_errors)))
        for e in sb_errors:
            print("  - %s" % e)
    else:
        print("shotbank.jsonl：未生成，跳过")

    if errors:
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
