# -*- coding: utf-8 -*-
"""示例库扩容：把 13 条 few_shots 扩到 ~55 条，并按话题/形态打标签。

为什么需要这一步：
    v3 的示例是 13 条通用样本，检索按长度挑，于是「同事抢功」和「我妈催婚」
    拿到的是同三条。模型学到的不是「怎么接这件事」，是「这三句的语感」。
    v4 的做法是按「话题 × 形态」矩阵铺样本，让检索能对题。
    docs 6.9.6 已经验证过三次：**样本的权重高于规则**。

为什么生成之后还要逐条硬校验：
    模型写出来的东西，第一眼往往像，第二眼就露馅——长度全一样、每句都工整、
    每条都给建议。这些是机器能判的，就不该靠人读。校验分两层：
        形状层：字数/句数落在 texture 的带内、无 emoji/markdown/列表。
        指纹层：不能和现有样本有 12 字重合、不能命中 anti_bits / 助手腔词表。
    校验通过的才进 shotbank；拒绝原因全量计数，写进 report.md。

LLM 失败时怎么降级：
    没有 key、超时、解析失败，都不抛异常。这条流水线宁可给 37 条能用的
    样本（13 种子 + 24 手写），也不能因为网络问题让 texture 和 report 交不出来。
    降级事实会写进报告，不假装生成成功。

可重跑 / 断点续：
    id 由「槽位序号 + 话题 + 形态」确定。重跑时已存在且仍通过校验的 id 直接跳过；
    只有缺失或已失效的槽位才再调 LLM。单次调用超时 30 秒，最多重试 3 次。
"""

from __future__ import annotations

import json
import math
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - 环境里一定装了 pyyaml
    yaml = None

from . import corpus
from .handwritten import POOL

PERSONA_DIR = corpus.ROOT / "packages" / "persona" / "brufen"
PERSONA_PATH = PERSONA_DIR / "persona.yaml"
SHOTBANK_PATH = PERSONA_DIR / "shotbank.jsonl"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
STATS_PATH = CACHE_DIR / "shotbank_stats.json"

TARGET_NEW = 42
LLM_TIMEOUT = 30
LLM_MAX_RETRIES = 3

# 话题标签直接对齐 services/api/app/voice/topics.py 的 12 个名字。
# 为什么复制一份而不是 import 后端：tools/ 是纯标准库 + pyyaml 的离线工具链，
# 不能因为后端依赖（httpx/pydantic settings）装不上就跑不了蒸馏。
# 改后端词表时这里要同步，report.md 有专门一行提醒。
TOPICS: list[tuple[str, tuple[str, ...]]] = [
    ("职场否定", ("领导", "老板", "上司", "主管", "甲方", "当众", "批评", "否定", "没灵魂", "格局", "方案")),
    ("加班", ("加班", "熬夜", "通宵", "996", "007", "大小周", "下班", "工时", "干到", "排期")),
    ("辞职", ("辞职", "离职", "裸辞", "想走", "跳槽", "优化", "毕业")),
    ("求职", ("面试", "校招", "求职", "简历", "找工作", "offer", "被裁", "失业", "投了", "转正")),
    ("功劳", ("抢功", "署名", "复述", "甩锅", "背锅", "同事", "排挤", "孤立", "群聊")),
    ("家庭", ("妈", "爸", "父母", "催婚", "催生", "相亲", "结婚", "亲戚", "过年", "家里", "家务")),
    ("感情", ("分手", "失恋", "前任", "对象", "恋爱", "冷战", "吵架", "喜欢", "结婚")),
    ("钱", ("工资", "钱", "涨薪", "房租", "存款", "穷", "月光", "贷款", "报销", "押金")),
    ("身体累", ("累", "疲惫", "撑不住", "没力气", "困", "失眠", "睡不着", "身体", "头疼", "难受")),
    ("自我否定", ("没用", "废物", "失败", "不配", "什么都做不好", "没意义", "没意思", "没救了", "算了")),
    ("社交", ("朋友", "同学", "聚会", "社恐", "尴尬", "群", "眼神", "没人")),
    ("荒唐提问", ("你会", "你能不能", "你是不是", "如果", "假设", "为什么", "怎么看待", "评价一下")),
]
DEFAULT_TOPIC = "闲聊"

# 任务给的 8 个话题维度 → 后端更细的 12 个标签。
COARSE_MAP: dict[str, str] = {
    "职场": "职场否定",
    "家庭": "家庭",
    "感情": "感情",
    "身体累": "身体累",
    "自我否定": "自我否定",
    "钱": "钱",
    "社交": "社交",
    "荒唐提问": "荒唐提问",
}

# 42 个槽位 = 8 话题 × 5–6 形态。刻意让「讲自己」只占 7/42、
# 「谐音链」只占 5/42——docs 6.9.4 说谐音是低频动作，样本里堆多了会变成杂技。
# 职场铺 6 个槽位，摊到后端 5 个更细的标签上，是为了让检索能命中「抢功/加班/辞职」。
SLOTS: list[tuple[str, str, str]] = [
    ("职场", "职场否定", "一句"),
    ("职场", "加班", "两句"),
    ("职场", "辞职", "三句"),
    ("职场", "求职", "讲自己"),
    ("职场", "功劳", "谐音链"),
    ("职场", "职场否定", "否定排位"),
    ("家庭", "家庭", "一句"),
    ("家庭", "家庭", "两句"),
    ("家庭", "家庭", "三句"),
    ("家庭", "家庭", "讲自己"),
    ("家庭", "家庭", "谐音链"),
    ("家庭", "家庭", "否定排位"),
    ("感情", "感情", "一句"),
    ("感情", "感情", "两句"),
    ("感情", "感情", "三句"),
    ("感情", "感情", "讲自己"),
    ("感情", "感情", "否定排位"),
    ("身体累", "身体累", "一句"),
    ("身体累", "身体累", "两句"),
    ("身体累", "身体累", "三句"),
    ("身体累", "身体累", "讲自己"),
    ("身体累", "身体累", "否定排位"),
    ("自我否定", "自我否定", "一句"),
    ("自我否定", "自我否定", "两句"),
    ("自我否定", "自我否定", "三句"),
    ("自我否定", "自我否定", "讲自己"),
    ("自我否定", "自我否定", "否定排位"),
    ("钱", "钱", "一句"),
    ("钱", "钱", "两句"),
    ("钱", "钱", "三句"),
    ("钱", "钱", "讲自己"),
    ("钱", "钱", "谐音链"),
    ("社交", "社交", "一句"),
    ("社交", "社交", "两句"),
    ("社交", "社交", "三句"),
    ("社交", "社交", "讲自己"),
    ("社交", "社交", "谐音链"),
    ("荒唐提问", "荒唐提问", "一句"),
    ("荒唐提问", "荒唐提问", "两句"),
    ("荒唐提问", "荒唐提问", "三句"),
    ("荒唐提问", "荒唐提问", "谐音链"),
    ("荒唐提问", "荒唐提问", "否定排位"),
]

# 每个形态给模型的硬指令。写成「做/不做」而不是形容词，因为形容词会被
# 模型翻译成另一种形容词（这是 v3 反复踩过的坑）。
FORM_HINT = {
    "一句": "只写一句。整条回复只能有一个句号（句末那个），中间不许出现句号、问号、感叹号或换行。不解释，不给建议。",
    "两句": "正好两句（两个句号，不许更多）。第一句接住，第二句落一个具体判断或动作，不解释理由。",
    "三句": "正好三句（三个句号，不许更多）。中间必须有一次跑题或自我推翻，最后一句要短。",
    "讲自己": "第一句接住，中间讲自己的一件具体小事（有物件/动作/数字），最后回到用户或直接停。",
    "谐音链": "抓用户话里一个词，换一个同音字，用「所以」推一步，落到自己身上。只演，不解释这个谐音。",
    "否定排位": "先说别人会以为的那个体面原因，用「不是因为」拆掉，再给一个更难看但更真的原因。",
}

# 校验用正则（与 services/api/app/quality.py 同口径，但这里不 import 后端）。
EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U0001F1E6-\U0001F1FF" "\u2600-\u27BF" "\uFE0F" "\u2B00-\u2BFF" "]"
)
MARKDOWN_RE = re.compile(r"(\*\*|^#{1,6}\s|```|^\s*[-*+]\s|^\s*\d+\.\s)", re.MULTILINE)
LIST_RE = re.compile(r"^\s*(\d+[.、)]|[-*+•])\s", re.MULTILINE)
SENT_SPLIT = re.compile(r"[。！？!?…\n]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 解释梗：前七个是 services/api/app/voice/scorer.py 的 _EXPLAIN_PUN_RE 原样语义；
# 后一组是抽检里发现的「半解释」——把谐音拆开讲（「就差一把火」「署的是他的名」）。
# 这条规则宁可误伤，也不让解释梗进示例库：它教模型「怎么解释梗」，方向正好相反。
PUN_EXPLAIN_RE = re.compile(
    r"(一个音|同音字?|念起来像|读起来像|谐音是|这词(以前)?写作|押韵"
    r"|只差|就差|差一个|差一把|换个字|换一个字|拆开|拆成|念作|读作|音同|音近"
    r"|同一个字|署的是|属的是|写的是同一个|说的是同一个)"
)
PARALLEL_RE = re.compile(r"(不是|并非|不只是)[^。！？\n]{0,14}(而是|是)")
NEG_RANK_RE = re.compile(r"不是.{0,16}(是|而是|因为)|不是因为")
SELF_OPENER_RE = re.compile(r"^(我上回|我去年|我以前|我那次|我上次|我小时候|我上个月|我当年|我曾经|我那会儿)")
SELF_OPINION_RE = re.compile(r"^我((觉得|想|认为|知道|猜|估计|看|说)|(也)?不是)")


# ---------------------------------------------------------------------------
# 本地小工具
# ---------------------------------------------------------------------------
def _hanzi(text: str) -> str:
    return "".join(CJK_RE.findall(text or ""))


def _hanzi_count(text: str) -> int:
    return len(_hanzi(text))


def _content_len(text: str) -> int:
    return len(re.sub(r"\s", "", text or ""))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text or "") if s.strip()]


def _self_story(text: str) -> bool:
    for seg in re.split(r"[。！？!?\n]+", text or ""):
        s = seg.strip().lstrip("（(「").strip()
        if len(s) >= 4 and s.startswith("我") and not SELF_OPINION_RE.match(s):
            return True
    return False


def detect_topic(user_text: str) -> str:
    """与后端 topics.detect 同一套规则：词表顺序靠前者优先，不做加权。"""
    t = user_text or ""
    for name, kws in TOPICS:
        if any(k in t for k in kws):
            return name
    return DEFAULT_TOPIC


def _longest_common(a: str, b: str, n: int = 12) -> bool:
    if len(a) < n or len(b) < n:
        return False
    for i in range(0, len(a) - n + 1):
        if a[i : i + n] in b:
            return True
    return False


def _normalize_for_copy(text: str) -> str:
    return re.sub(r"[\s。，、！？…“”「」『』（）()：:；;]", "", text or "")


def _form_ok(reply: str, form: str) -> tuple[bool, str]:
    sents = _sentences(reply)
    n = len(sents)
    if form == "一句" and n != 1:
        return False, "形态不符：要求一句，实得 %d 句" % n
    if form == "两句" and n != 2:
        return False, "形态不符：要求两句，实得 %d 句" % n
    if form == "三句" and n < 3:
        return False, "形态不符：要求三句，实得 %d 句" % n
    if form == "讲自己" and not _self_story(reply):
        return False, "形态不符：要求讲自己，未检测到自述句"
    if form == "否定排位" and not NEG_RANK_RE.search(reply):
        return False, "形态不符：未检测到「不是因为…」的否定排位"
    if form == "谐音链" and not ("所以" in reply or "是" in reply):
        return False, "形态不符：谐音链没有落点句"
    return True, ""


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
class Validator:
    def __init__(self, texture: dict, persona: dict, seeds: list[str], existing: list[str]):
        bands = texture.get("bands") or {}
        chars = bands.get("reply_chars") or {}
        sents = bands.get("sentences") or {}
        self.char_min = max(4, int((chars.get("p10") or 12) * 0.8))
        self.char_max = max(self.char_min + 4, math.ceil((chars.get("p90") or 39) * 1.2))
        self.sent_min = max(1, int(sents.get("p10") or 1))
        self.sent_max = max(self.sent_min + 1, int(sents.get("p90") or 2) + 1)
        self.anti_bits = [str(x) for x in (persona.get("anti_bits") or [])]
        self.anti_phrasings = [
            str(x) for x in ((persona.get("anti_ai_flavor") or {}).get("phrasings") or [])
        ]
        self.known = [_normalize_for_copy(x) for x in [*seeds, *existing] if x]
        self.reject_reasons: dict[str, int] = {}

    def reject(self, reason: str) -> None:
        self.reject_reasons[reason] = self.reject_reasons.get(reason, 0) + 1

    def validate(
        self, user: str, reply: str, backend_topic: str, form: str, exclude_norm: str | None = None
    ) -> tuple[bool, str]:
        user = (user or "").strip()
        reply = (reply or "").strip()
        if not user or not reply:
            return False, "空内容"
        if not (6 <= _content_len(user) <= 80):
            return False, "user 长度越界"
        n_char = _hanzi_count(reply)
        if not (self.char_min <= n_char <= self.char_max):
            return False, "字数越界（%d 不在 %d–%d）" % (n_char, self.char_min, self.char_max)
        sents = _sentences(reply)
        if not (self.sent_min <= len(sents) <= self.sent_max):
            return False, "句数越界（%d 不在 %d–%d）" % (len(sents), self.sent_min, self.sent_max)
        if EMOJI_RE.search(reply):
            return False, "emoji"
        if MARKDOWN_RE.search(reply) or LIST_RE.search(reply):
            return False, "Markdown/列表"
        lowered = reply.lower()
        for bit in self.anti_bits:
            if bit and bit.lower() in lowered:
                return False, "禁用词「%s」" % bit
        for phrase in self.anti_phrasings:
            p = phrase.strip()
            if len(p) >= 4 and p.lower() in lowered:
                return False, "助手腔「%s」" % p
        if PUN_EXPLAIN_RE.search(reply):
            return False, "解释了自己的梗"
        if len(PARALLEL_RE.findall(reply)) >= 2:
            return False, "对称排比（像作文）"
        if len(sents) >= 3:
            lens = [_hanzi_count(s) for s in sents]
            if lens and max(lens) - min(lens) <= 2:
                return False, "句长太齐（像排比）"
        # ---- 词法层硬规则（2026-11-26 抽检后补） ----
        # 1) 末尾必须有句末标点。示例是教断句的，缺句号的样本在教相反的事。
        if reply[-1] not in "。！？…":
            return False, "末尾缺句末标点"
        # 2) 不许用 ASCII 空格断句。中文口语没有空格，出现即为标点错误。
        if " " in reply:
            return False, "用 ASCII 空格断句"
        # 3) 单句超过 30 个汉字 = 一整句长句，像段落不像消息。
        for sent in sents:
            if _hanzi_count(sent) > 30:
                return False, "单句超过 30 字"
        # 4) 解释/半解释自己的梗（人设明令禁止）。
        if PUN_EXPLAIN_RE.search(reply):
            return False, "解释了自己的梗"
        ok, why = _form_ok(reply, form)
        if not ok:
            return False, why
        if detect_topic(user) != backend_topic:
            return False, "user 话题与槽位不符（detect=%s）" % detect_topic(user)
        norm = _normalize_for_copy(reply)
        for known in self.known:
            if exclude_norm is not None and known == exclude_norm:
                continue
            if _longest_common(norm, known, 12):
                return False, "与现有样本 12 字重合"
        return True, ""

    def add_known(self, reply: str) -> None:
        norm = _normalize_for_copy(reply)
        if norm:
            self.known.append(norm)


# ---------------------------------------------------------------------------
# 手写兜底池：24 条，LLM 不可用或槽位反复失败时用
# ---------------------------------------------------------------------------
# 这些是按「话题 × 形态」手写的原创样本，不是他的原话。写的时候对着
# texture 的带（9–47 汉字、1–3 句）来，避免降级后还要放宽校验。
HANDWRITTEN = POOL


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------
def _load_env() -> dict:
    env: dict[str, str] = {}
    path = corpus.ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    import os

    for key in ("OAD_PROVIDER", "OAD_DEEPSEEK_API_KEY", "OAD_DEEPSEEK_BASE_URL", "OAD_DEEPSEEK_MODEL"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _llm_available() -> tuple[bool, dict, str]:
    env = _load_env()
    provider = (env.get("OAD_PROVIDER") or "").lower()
    key = env.get("OAD_DEEPSEEK_API_KEY") or ""
    if provider != "deepseek":
        return False, env, "OAD_PROVIDER=%s，不是 deepseek" % (provider or "空")
    if not key:
        return False, env, "OAD_DEEPSEEK_API_KEY 未配置"
    return True, env, ""


def _chat(env: dict, system: str, user: str, temperature: float = 1.0, max_tokens: int = 320) -> str:
    base = (env.get("OAD_DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = env.get("OAD_DEEPSEEK_MODEL") or "deepseek-chat"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + (env.get("OAD_DEEPSEEK_API_KEY") or ""),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data["choices"][0]["message"]["content"] or "").strip()


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _build_system_prompt(persona: dict, texture: dict, seeds: list[dict]) -> str:
    bands = texture.get("bands") or {}
    chars = bands.get("reply_chars") or {}
    sents = bands.get("sentences") or {}
    char_min = max(4, int((chars.get("p10") or 12) * 0.8))
    char_max = max(char_min + 4, int((chars.get("p90") or 39) * 1.2))
    sent_max = max(2, int(sents.get("p90") or 2) + 1)
    one_liner = (persona.get("worldview") or {}).get("one_liner") or ""
    engine = (persona.get("speech_engine") or {}).get("core") or ""
    rhythm = (persona.get("speech_rhythm") or {}).get("core") or ""
    anti_bits = "、".join(str(x) for x in (persona.get("anti_bits") or [])[:18])
    phrasings = "、".join(
        str(x) for x in ((persona.get("anti_ai_flavor") or {}).get("phrasings") or [])[:16]
    )
    examples = []
    for i, shot in enumerate(seeds[:4]):
        examples.append("用户：%s\n他：%s" % (shot.get("user", ""), str(shot.get("reply", "")).strip()))
    return (
        "你是一个中文语料编辑，不是聊天助手。你要为一个人格包写「用户的一句话 + 他的回复」。\n"
        "这个人格不是真人复刻，是一种「打工人的胡说」风格的抽象。\n\n"
        "【这个人是谁】\n%s\n\n【他的说话引擎】\n%s\n\n【语流】\n%s\n\n"
        "【硬约束】\n"
        "1. 回复只用汉字和中文标点，不许 emoji、Markdown、分点列表、括号里的解释。\n"
        "2. 长度必须在 %d–%d 个汉字之间；总句数 1–%d 句；必须有一句是短的。\n"
        "3. 多数回复不给建议、不安慰、不总结。不要「首先/其次/总之/最后」。\n"
        "4. 不许出现这些词：%s。\n"
        "5. 不许出现这些助手腔：%s。\n"
        "6. 谐音只能演出来，绝对不能解释「同音/念起来像/这词写作」。\n"
        "7. 不要工整排比，不要每句一样长。要像微信里随手打的一段话。\n"
        "8. 起手式要换着来，不许每条都用「我上回/我去年」开头。\n\n"
        "【风格参照，只学节奏和落点，不许抄任何情节或句子】\n"
        + "\n---\n".join(examples)
        + "\n\n【输出格式】\n只输出一个 JSON 对象：{\"user\": \"...\", \"reply\": \"...\"}。"
        "不要输出别的字段，不要解释。"
    ) % (
        one_liner.strip(), engine.strip(), rhythm.strip(),
        char_min, char_max, sent_max, anti_bits, phrasings,
    )


def _build_user_prompt(coarse: str, backend_topic: str, form: str) -> str:
    kws = dict(TOPICS).get(backend_topic, ())
    return (
        "这次的任务：\n"
        "- 话题：%s（%s）。用户那句话里要自然出现这组词里的至少一个：%s。\n"
        "- 形态：%s。%s\n"
        "- 用户是一个普通打工人，在微信上跟朋友吐槽，不是来求诊断的。\n"
        "现在写 1 条。"
        % (coarse, backend_topic, "、".join(kws[:6]), form, FORM_HINT[form])
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _load_persona() -> dict:
    if yaml is None:
        return {}
    try:
        return yaml.safe_load(PERSONA_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _seed_rows(persona: dict) -> list[dict]:
    out = []
    for i, shot in enumerate(persona.get("few_shots") or []):
        user = str(shot.get("user") or "").strip()
        reply = str(shot.get("reply") or "").strip()
        if not user or not reply:
            continue
        out.append(
            {
                "id": "seed-%02d" % i,
                "topic": detect_topic(user),
                "form": _infer_form(reply),
                "user": user,
                "reply": reply,
                "level": str(shot.get("level") or "L0"),
                "chars": _hanzi_count(reply),
                "sentences": len(_sentences(reply)),
                "has_self_story": _self_story(reply),
                "origin": "persona-seed",
                "seed_from": None,
            }
        )
    return out


def _infer_form(reply: str) -> str:
    if _self_story(reply):
        return "自述"
    n = len(_sentences(reply))
    if n <= 1:
        return "一句"
    if n == 2:
        return "两句"
    return "多句"


def _read_existing() -> dict[str, dict]:
    if not SHOTBANK_PATH.is_file():
        return {}
    out: dict[str, dict] = {}
    for line in SHOTBANK_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"])] = row
    return out


def _write_rows(rows: list[dict]) -> None:
    PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    SHOTBANK_PATH.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def _fallback_pool(validator: Validator) -> list[dict]:
    """手写池按槽位顺序消费。先匹配 (topic, form)，再退到同 topic，最后任意。"""
    pool = []
    for i, item in enumerate(HANDWRITTEN):
        reply = item["reply"].strip()
        user = item["user"].strip()
        if not (validator.char_min <= _hanzi_count(reply) <= validator.char_max):
            continue
        if not (validator.sent_min <= len(_sentences(reply)) <= validator.sent_max):
            continue
        ok, _why = _form_ok(reply, item["form"])
        if not ok or detect_topic(user) != item["topic"]:
            continue
        pool.append(dict(item, _idx=i))
    return pool


def build_shotbank(texture: dict, *, verbose: bool = False) -> dict:
    persona = _load_persona()
    seeds = _seed_rows(persona)
    seed_replies = [s["reply"] for s in seeds]
    existing_rows = _read_existing()
    # 已生成的非 seed 行先作为「现有样本」，避免重跑时新候选和它们撞 12 字。
    existing_new = [r for r in existing_rows.values() if r.get("origin") != "persona-seed"]
    validator = Validator(texture, persona, seed_replies, [r.get("reply", "") for r in existing_new])

    stats = {
        "target_new": TARGET_NEW,
        "llm_available": False,
        "llm_reason": "",
        "llm_calls": 0,
        "llm_errors": 0,
        "generated_ok": 0,
        "generated_from_cache": 0,
        "fallback_used": 0,
        "rejected_total": 0,
        "reject_reasons": {},
        "degraded": False,
        "degraded_reason": "",
        "seed_count": len(seeds),
        "total": 0,
        "accepted_new": 0,
    }
    available, env, reason = _llm_available()
    stats["llm_available"] = available
    stats["llm_reason"] = reason
    if not available:
        stats["degraded"] = True
        stats["degraded_reason"] = "无可用 LLM：" + reason

    fallback_pool = _fallback_pool(validator)
    fallback_used_idx: set[int] = set()

    cand_rows: list[dict] = []
    def _take_fallback(backend_topic: str, form: str) -> dict | None:
        """挑一条还没用过的手写样本：先精确匹配，再同话题，最后任意。"""
        for match in ("exact", "topic", "any"):
            for item in fallback_pool:
                if item["_idx"] in fallback_used_idx:
                    continue
                if match == "exact" and (item["topic"], item["form"]) != (backend_topic, form):
                    continue
                if match == "topic" and item["topic"] != backend_topic:
                    continue
                fallback_used_idx.add(item["_idx"])
                return item
        return None

    for idx, (coarse, backend_topic, form) in enumerate(SLOTS, 1):
        slot_id = "sb-%02d" % idx
        # 断点续：已有且仍通过校验的直接留用。
        cached = existing_rows.get(slot_id)
        if cached and cached.get("origin") != "persona-seed":
            cached_reply = str(cached.get("reply") or "")
            ok, why = validator.validate(
                str(cached.get("user") or ""),
                cached_reply,
                backend_topic,
                form,
                exclude_norm=_normalize_for_copy(cached_reply),
            )
            if ok:
                validator.add_known(cached_reply)
                cand_rows.append(cached)
                stats["generated_from_cache"] += 1
                continue
            if verbose:
                print("      槽位 %s 缓存失效：%s，重新生成" % (slot_id, why))

        row = None
        if available:
            system = _build_system_prompt(persona, texture, persona.get("few_shots") or [])
            user_prompt = _build_user_prompt(coarse, backend_topic, form)
            last_why = ""
            for attempt in range(1, LLM_MAX_RETRIES + 1):
                stats["llm_calls"] += 1
                try:
                    raw = _chat(env, system, user_prompt)
                except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
                    stats["llm_errors"] += 1
                    last_why = "LLM 网络错误：%s" % type(exc).__name__
                    time.sleep(0.8)
                    continue
                except Exception as exc:  # 上游返回异常 JSON 等
                    stats["llm_errors"] += 1
                    last_why = "LLM 调用异常：%s" % type(exc).__name__
                    time.sleep(0.8)
                    continue
                parsed = _parse_json(raw)
                if not parsed:
                    last_why = "JSON 解析失败"
                    continue
                user = str(parsed.get("user") or "").strip()
                reply = str(parsed.get("reply") or "").strip()
                ok, why = validator.validate(user, reply, backend_topic, form)
                if ok:
                    validator.add_known(reply)
                    row = {
                        "id": slot_id,
                        "coarse_topic": coarse,
                        "topic": backend_topic,
                        "form": form,
                        "user": user,
                        "reply": reply,
                        "level": "L0",
                        "chars": _hanzi_count(reply),
                        "sentences": len(_sentences(reply)),
                        "has_self_story": _self_story(reply),
                        "origin": "distilled-v4",
                        # schema 约定的种子引用默认为 null；这里不假装某一条示例
                        # 是从某个 few_shot 派生的——它是按话题×形态矩阵生成的。
                        "seed_from": None,
                    }
                    stats["generated_ok"] += 1
                    break
                last_why = why
                validator.reject(why)
                stats["rejected_total"] += 1
            if verbose and row is None:
                print("      槽位 %s 生成失败（%s），用手写兜底" % (slot_id, last_why))
        if row is None:
            fb = _take_fallback(backend_topic, form)
            if fb is not None:
                row = {
                    "id": slot_id,
                    "coarse_topic": coarse,
                    "topic": backend_topic,
                    "form": form,
                    "user": fb["user"],
                    "reply": fb["reply"],
                    "level": "L0",
                    "chars": _hanzi_count(fb["reply"]),
                    "sentences": len(_sentences(fb["reply"])),
                    "has_self_story": _self_story(fb["reply"]),
                    "origin": "handwritten-fallback",
                    "seed_from": None,
                }
                validator.add_known(fb["reply"])
                stats["fallback_used"] += 1
            else:
                stats["rejected_total"] += 1
                validator.reject("手写兜底池耗尽")
        if row is not None:
            cand_rows.append(row)

    # 全局自述开头配额：≤1/3。超出时从后往前撤（后面的槽位优先级低），
    # 被撤掉的槽位如果还有手写样本就换一条，否则留空。
    def _ratio(rows: list[dict]) -> float:
        if not rows:
            return 0.0
        return sum(1 for r in rows if SELF_OPENER_RE.match(r["reply"])) / len(rows)

    while cand_rows and _ratio(cand_rows) > 1 / 3:
        for i in range(len(cand_rows) - 1, -1, -1):
            if SELF_OPENER_RE.match(cand_rows[i]["reply"]):
                dropped = cand_rows.pop(i)
                stats["rejected_total"] += 1
                validator.reject("自述开头超 1/3 配额")
                if verbose:
                    print("      撤掉自述开头样本 %s" % dropped["id"])
                # 尝试用手写样本补回同话题
                fb = _take_fallback(dropped["topic"], dropped["form"])
                if fb is not None:
                    cand_rows.insert(
                        i,
                        {
                            "id": dropped["id"],
                            "coarse_topic": dropped.get("coarse_topic", ""),
                            "topic": dropped["topic"],
                            "form": dropped["form"],
                            "user": fb["user"],
                            "reply": fb["reply"],
                            "level": "L0",
                            "chars": _hanzi_count(fb["reply"]),
                            "sentences": len(_sentences(fb["reply"])),
                            "has_self_story": _self_story(fb["reply"]),
                            "origin": "handwritten-fallback",
                            "seed_from": None,
                        },
                    )
                    validator.add_known(fb["reply"])
                    stats["fallback_used"] += 1
                break
        else:
            break

    rows = seeds + cand_rows
    _write_rows(rows)
    stats["total"] = len(rows)
    stats["accepted_new"] = len(cand_rows)
    stats["reject_reasons"] = dict(sorted(validator.reject_reasons.items(), key=lambda x: -x[1]))
    if stats["fallback_used"] and not stats["degraded"]:
        stats["degraded"] = True
        stats["degraded_reason"] = "%d 个槽位改用或补用手写样本" % stats["fallback_used"]
    # 把每一次运行压成一行历史。为什么需要：--all 重跑时 42 个槽位全部命中缓存，
    # llm_calls 和拒绝数都会变 0；如果只写「本次」，报告里「生成多少/拒绝多少」
    # 就永远显示 0，等于把第一次真实生成的证据抹掉。
    history: list[dict] = []
    if STATS_PATH.is_file():
        try:
            history = list((json.loads(STATS_PATH.read_text(encoding="utf-8")) or {}).get("history") or [])
        except ValueError:
            history = []
    history.append(
        {
            "llm_calls": stats["llm_calls"],
            "llm_errors": stats["llm_errors"],
            "generated_ok": stats["generated_ok"],
            "generated_from_cache": stats["generated_from_cache"],
            "fallback_used": stats["fallback_used"],
            "rejected_total": stats["rejected_total"],
            "reject_reasons": stats["reject_reasons"],
            "total": stats["total"],
            "accepted_new": stats["accepted_new"],
            "degraded": stats["degraded"],
            "degraded_reason": stats["degraded_reason"],
        }
    )
    stats["history"] = history[-20:]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(
            "      LLM 可用=%s 调用=%d 成功=%d 缓存=%d 手写兜底=%d 拒绝=%d"
            % (
                stats["llm_available"],
                stats["llm_calls"],
                stats["generated_ok"],
                stats["generated_from_cache"],
                stats["fallback_used"],
                stats["rejected_total"],
            )
        )
        if stats["degraded"]:
            print("      ⚠ 降级：%s" % stats["degraded_reason"])
    return stats
