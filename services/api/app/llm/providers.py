"""模型适配层。

三个 provider：
  - mock   : 零依赖、零网络、零 key，clone 下来就能聊。默认。
  - ollama : 本地模型，隐私最好。
  - openai : 任何 OpenAI 兼容端点（DeepSeek / Qwen / Moonshot / OpenAI）。

为什么不是真流式：
  长度与合规校验必须在用户看到之前完成。所以统一 complete() 拿全文，
  校验通过后再按字符回放成 SSE。90 字以内的回复，这点延迟换质量值得。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import AsyncIterator, Protocol

import httpx

from ..config import settings


class Provider(Protocol):
    name: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        top_p: float,
        presence_penalty: float,
        max_tokens: int = 400,
    ) -> str: ...


# ===========================================================================
# mock：把人格装置的模板库实现成一个确定性生成器
# ===========================================================================
# 它不是「假装有模型」，而是一个可离线运行的基线。
# 作用有二：1) 任何人 clone 即可体验；2) 评测时有可对比的下限。
# ===========================================================================

_TOPICS: list[tuple[str, list[str]]] = [
    ("overtime", ["加班", "熬夜", "通宵", "996", "007", "大小周", "下班", "工时", "干到"]),
    ("boss_criticism", ["领导", "老板", "上司", "甲方", "主管", "否定", "批", "骂", "没有灵魂", "格局"]),
    ("resign", ["辞职", "离职", "裸辞", "想走", "跳槽"]),
    ("jobhunt", ["面试", "校招", "求职", "简历", "找工作", "offer", "被裁", "失业", "投了"]),
    ("marriage", ["催婚", "催生", "相亲", "结婚", "对象", "生娃", "爸妈", "亲戚"]),
    ("tired", ["累", "疲惫", "撑不住", "没力气", "困", "emo", "空"]),
    ("money", ["工资", "钱", "涨薪", "房租", "存款", "穷", "月光", "贷款"]),
    ("colleague", ["同事", "甩锅", "背锅", "排挤", "孤立", "抢功", "小组"]),
    ("breakup", ["分手", "失恋", "前任", "冷战", "吵架", "相亲对象"]),
    ("selfworth", ["没用", "废物", "失败", "不配", "什么都做不好", "没意义"]),
]

_TEMPLATES: dict[str, dict[str, list[str]]] = {
    # 模板一律按 v2 语流写：短句、可断句、讲自己、硬着陆。
    # 上一版全是「洞察式金句」（「甩锅这件事，考验的不是人品，是留痕意识」），
    # 每句都完整、都对称、都像推文——那正是要消掉的 AI 味。
    "overtime": {
        "hook": [
            "加班到几点。",
            "这个点下班，在你们公司算早退吗。",
            "又加了。",
        ],
        "translate": [
            "排期的时候就没给你留人的位置。",
            "我上个月也这样，回家坐门口换了十分钟鞋，不知道在磨什么。",
            "不是你慢。这活本来就是三个人的。",
        ],
        "land": [
            "今晚别复盘人生，洗澡睡觉。",
            "明天把今天干的活写成三条发领导。让他看见，而不是感觉到。",
            "这周挑一天准点走。不用打招呼。",
        ],
    },
    "boss_criticism": {
        "hook": [
            "「没灵魂」这词挺损的。",
            "当众说。",
            "这评价最省力。",
        ],
        "translate": [
            "他大概率自己也没想清楚要什么。",
            "我那次挨念，他后面讲了什么我全没听进去。",
            "当众说，是为了让在场的人知道他在管事。跟你做得好不好关系不大。",
        ],
        "land": [
            "明天把「灵魂」拆成三个能交的东西发他，让他挑。",
            "下次听到这句就问：您希望它更像哪个案例。",
            "把原话记下来，连同日期。以后自证用得上。",
        ],
    },
    "resign": {
        "hook": [
            "先别辞。",
            "辞职这念头一年来三百次，都在周一。",
            "想走是对的。",
        ],
        "translate": [
            "生气是免费的，社保不是。",
            "我辞职那次是先请了两天年假，在家睡到中午，第三天自己就想清楚了。",
            "顺序可以调一下。",
        ],
        "land": [
            "把简历弄好再说。",
            "先算一下手里的钱能撑几个月。",
            "别在气头上提。等他心情好再说。",
        ],
    },
    "colleague": {
        "hook": [
            "署名改了。",
            "这种活他也抢。",
            "抢功的一般干活不行。",
        ],
        "translate": [
            "他不是针对你，他是针对所有人。这叫公平。",
            "我上回也被人拿走一次，回工位把原稿另存了一份，名字后面加了日期。那份东西后来没再打开过。",
            "你把活干了不留痕，功劳和责任就一起归别人了。",
        ],
        "land": [
            "以后口头安排，回一句「我确认一下：A 我做，B 你做」。",
            "把这次的时间线写五行，自己留着。",
            "下次分工，先说清你负责到哪一步。",
        ],
    },
    "breakup": {
        "hook": [
            "分了。",
            "最难受的不是失去。",
            "这事快不了。",
        ],
        "translate": [
            "你怀念的可能不是他，是有人等你回家。",
            "我分开那阵子最不习惯的是点外卖只点一份，凑不够起送价。",
            "反复想「如果当时」是大脑在找一个能控制的原因。没有。",
        ],
        "land": [
            "把那几张照片挪到一个不常翻的文件夹。不删，只挪。",
            "明天约个朋友吃饭，只吃饭，不聊这事。",
            "两周内不做关于他的任何决定。",
        ],
    },
    "selfworth": {
        "hook": [
            "「没用」这词你用得比谁都狠。",
            "这句我不接。",
            "评价自己你下手太重。",
        ],
        "translate": [
            "你不是没用。你是累了很久，用一句最省力的话骂自己。",
            "「什么都做不好」拆开看，通常只有一两件事，其余是被这句话带走的。",
            "低电量的时候，判断力也会跟着掉。",
        ],
        "land": [
            "今晚先睡。明天找个具体的人说一句实话。",
            "写三件今天做成的事，多小都算。不是为了正能量，是为了校准。",
            "明天做一件五分钟能做完的事，做完划掉。",
        ],
    },
    "default": {
        "hook": [
            "这事听着不大，但在你脑子里转很久了。",
            "先说结论：不是你太敏感。",
            "嗯。",
        ],
        "translate": [
            "很多事难受，不是因为结果坏，是因为一直没被说清楚。",
            "我遇到这种也说不清，就先去洗了个碗。",
            "说不清就先不说。",
        ],
        "land": [
            "今晚先别下结论。",
            "写成三行：发生了什么、你在意什么、明天做什么。写完放下。",
            "找一个具体的人说一遍。说出来和想是两件事。",
        ],
    },
}

_L2_TEMPLATES = {
    # L2 不走「讲个段子让你笑」，也不走「你很棒」。
    # 只做一件事：把评价的尺子换掉，再说一句具体的话。
    "hook": ["这句我不接。", "先停一下。", "这句我不翻译成道理。"],
    "translate": [
        "你现在不是要分析，是要有人在旁边。",
        "能说出这句话，说明你撑了很久。不是你脆弱。",
        "不是性格问题。是消耗太久了。",
    ],
    "land": [
        "今天别给自己下结论。找个人说一句实话，「我最近不太好」就够。",
        "身边没人就打 12356，24 小时有人接。这不是小题大做。",
        "今晚只做一件事：待在有人的地方。",
    ],
}

_DEFAULT_TOPIC = "default"

# 「更损 / 更暖」两档的通用替换句。取向与 persona 一致：
# 损档只损处境和系统，永不动用户；暖档去掉嘲讽但不许变成空口安慰。
_SPICIER_TRANSLATE = [
    "离谱的是，一个连标准都写不出来的评价，能当结论用。",
    "他被要求有品位，你被要求猜他的品位。",
    "问题不在方案。在这套流程把「我说不好」当成「你做得差」。",
]

_WARMER_HOOKS = [
    "被当众这么说，挺难接的。",
    "当面听这话，谁都得堵一会儿。",
    "这种时候心里堵一下，正常。",
]

_WARMER_TRANSLATE = [
    "不是你做得差，是没人把标准说清楚。",
    "难受是因为你在意。",
    "你挺认真对待这事的。这不是缺点。",
]


def _pick(options: list[str], seed: str) -> str:
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def _detect_topic(text: str) -> str:
    for topic, kws in _TOPICS:
        if any(k in text for k in kws):
            return topic
    return _DEFAULT_TOPIC


_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _compose(hook: str, translate: str, land: str, limit: int = 3) -> str:
    """按句数配额组装：先保 hook 和 land，translate 是可牺牲的解释层。

    不能先拼再截——截断会把句子剪在半路，读起来像断线。
    """
    h = _sentences(hook)[:1]
    t = _sentences(translate)[:1]
    l = _sentences(land)

    parts = h + t + l
    if len(parts) > limit:
        parts = h + l
    if len(parts) > limit:
        parts = h + l[: limit - 1]
    out = "。".join(p.rstrip("。！？") for p in parts)
    return out + "。"


class MockProvider:
    name = "mock"

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        top_p: float,
        presence_penalty: float,
        max_tokens: int = 400,
    ) -> str:
        await asyncio.sleep(0.05)  # 让前端流式有个可感知的节奏
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break

        level_match = re.search(r"(?:本轮等级】|本轮到)\s*(L[0-3])", system)
        level = level_match.group(1) if level_match else "L0"

        # 「更损 / 更暖」在真模型里是 prompt 指令；mock 没有理解力，
        # 必须自己读指令改选词，否则默认 provider 下两个按钮点了没反应。
        # 匹配用「损度提/暖度提」而不是整句：文案会改，语义锚点不变。
        variant = "default"
        if "损度提" in system:
            variant = "spicier"
        elif "暖度提" in system:
            variant = "warmer"

        # 种子只取「用户原话 + 等级」。不掺 system：否则变体指令会改变
        # 取词种子，让本应完全一样的两档（如低落态下的「更损」）产出不同。
        seed = hashlib.sha256((user_text + "|" + level).encode("utf-8")).hexdigest()

        if level == "L2":
            # L2 是支持性回复，允许比日常多一句——安全信息要说完
            return _compose(
                _pick(_L2_TEMPLATES["hook"], seed),
                _pick(_L2_TEMPLATES["translate"], seed + "t"),
                _pick(_L2_TEMPLATES["land"], seed + "l"),
                limit=4,
            )

        topic = _detect_topic(user_text)
        tpl = _TEMPLATES.get(topic, _TEMPLATES[_DEFAULT_TOPIC])
        hook = _pick(tpl["hook"], seed)
        land = _pick(tpl["land"], seed + "l")
        translate = _pick(tpl["translate"], seed + "t")

        if level == "L1":
            # 情绪低落时只留两句：接住 + 落地，不解释。
            # 这一档不响应「更损」；「更暖」也不用换 hook——暖档 hook 是为
            # 「被当众否定」写的，套到泛化的低落上会答非所问。
            return _compose(hook, "", land, limit=2)

        if variant == "warmer":
            # 暖档：去掉嘲讽那一层，换成对处境的确认；落地只留最具体的一句
            return _compose(
                _pick(_WARMER_HOOKS, seed + "wh"),
                _pick(_WARMER_TRANSLATE, seed + "wt"),
                _sentences(land)[0] if _sentences(land) else land,
                limit=3,
            )

        if variant == "spicier":
            # 损档：把锋利的那句放最前，末尾的判据保留，损的是处境不是人
            return _compose(_pick(_SPICIER_TRANSLATE, seed + "st"), "", land, limit=3)

        return _compose(hook, translate, land, limit=3)


# ===========================================================================
# OpenAI 兼容（含 DeepSeek：协议一致，端点与模型名不同）
# ===========================================================================
def _explain_http_error(status: int, body: str, provider: str) -> str:
    """把上游状态码翻成能直接照着做的中文，别把原始 JSON 甩给用户。"""
    hints = {
        401: "API key 无效或已失效",
        402: "账户余额不足",
        403: "该 key 没有访问这个模型的权限",
        404: "模型名或端点路径不对",
        429: "触发限流或配额用尽，稍后再试",
        500: "上游服务内部错误",
        503: "上游服务过载",
    }
    hint = hints.get(status, "上游返回了非 2xx 状态")
    snippet = body.strip().replace("\n", " ")[:160]
    return f"{provider} 调用失败（HTTP {status}：{hint}）{('｜' + snippet) if snippet else ''}"


class OpenAIProvider:
    name = "openai"
    key_env = "OAD_OPENAI_API_KEY"

    def __init__(self) -> None:
        self.base_url = settings.openai_base_url.rstrip("/")
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        top_p: float,
        presence_penalty: float,
        max_tokens: int = 400,
    ) -> str:
        if not self.api_key:
            raise RuntimeError(
                f"{self.key_env} 未设置。要么配置它，要么把 OAD_PROVIDER 设为 mock 或 ollama。"
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": temperature,
            "top_p": top_p,
            "presence_penalty": presence_penalty,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            if r.status_code >= 400:
                raise RuntimeError(_explain_http_error(r.status_code, r.text, self.name))
            data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek 官方 API：协议与 OpenAI 一致，复用同一个实现。

    端点用 https://api.deepseek.com（带不带 /v1 都能通），模型默认 deepseek-chat。
    """

    name = "deepseek"
    key_env = "OAD_DEEPSEEK_API_KEY"

    def __init__(self) -> None:
        self.base_url = settings.deepseek_base_url.rstrip("/")
        self.api_key = settings.deepseek_api_key
        self.model = settings.deepseek_model


# ===========================================================================
# Ollama
# ===========================================================================
class OllamaProvider:
    name = "ollama"

    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        top_p: float,
        presence_penalty: float,
        max_tokens: int = 400,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "presence_penalty": presence_penalty,
                "num_predict": max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        return (data.get("message", {}).get("content") or "").strip()


def get_provider(name: str | None = None) -> Provider:
    key = (name or settings.provider or "mock").lower()
    if key == "ollama":
        return OllamaProvider()
    if key == "deepseek":
        return DeepSeekProvider()
    if key in ("openai", "qwen", "moonshot", "compatible"):
        return OpenAIProvider()
    return MockProvider()


async def stream_text(text: str, chunk_chars: int = 2, delay: float = 0.012) -> AsyncIterator[str]:
    """把已校验的整段文本回放成流。"""
    for i in range(0, len(text), chunk_chars):
        yield text[i : i + chunk_chars]
        await asyncio.sleep(delay)
