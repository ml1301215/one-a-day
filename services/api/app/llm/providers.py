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
    "overtime": {
        "hook": [
            "加班这件事最荒诞的地方，是它连加班费都不一定算得清。",
            "你不是在加班，你是在给公司的排期做无偿担保。",
            "今天这个点下班，已经算早退了——按你们公司的日历。",
        ],
        "translate": [
            "累不是你不努力，是排期本来就没留人的位置。",
            "身体先抗议，说明它比你更早看懂了这份工作。",
            "你以为是效率问题，其实是人数问题。",
        ],
        "land": [
            "今晚别复盘人生，先吃东西洗澡睡觉。明天再决定要不要生气。",
            "明天上班第一件事，把今天做的事写成三条，发给领导。让他看见，而不是感觉到。",
            "这周挑一天准点走。不用宣布，走就行。",
        ],
    },
    "boss_criticism": {
        "hook": [
            "「没有灵魂」这四个字，是所有评价里最省力的一种。",
            "灵魂这东西，一般在改到第七版的时候自动离职。",
            "他说这话的时候，大概率是他自己昨晚没睡好。",
        ],
        "translate": [
            "翻译过来是「我说不清要什么，但我知道这不是」。这是需求问题，不是你的问题。",
            "你的方案有没有灵魂我不知道，你的肝肯定有意见。",
            "当众说，是为了让在场的人知道他在管事。跟你做得好不好关系不大。",
        ],
        "land": [
            "明天把「灵魂」拆成三个能交付的东西发他，让他挑一个。挑不出来，没灵魂的是需求。",
            "下次再听到这句，就问：您希望它更像哪个案例？把评价逼成需求。",
            "把这句话原样记下来，连同日期。以后自证的时候用得上。",
        ],
    },
    "resign": {
        "hook": [
            "辞职这个念头，一年里会来三百次，通常都在周一。",
            "先别辞。愤怒是免费的，社保不是。",
            "想走是对的，只是顺序可以调一下。",
        ],
        "translate": [
            "你不是想辞职，你是想停止被这样对待。这两件事不一样。",
            "现在辞，是替别人的管理问题付违约金。",
            "真正的问题不是这份工作，是你已经很久没有一件自己说了算的事了。",
        ],
        "land": [
            "把简历更新完，再决定要不要生气。更新简历不花钱，也不代表你要走。",
            "这周约一个同行喝咖啡，只问一句：你们那儿最近招人吗。",
            "先算一遍：卡里余额除以每月固定支出，是多少个月。这个数字比情绪可靠。",
        ],
    },
    "jobhunt": {
        "hook": [
            "面试这件事，本质是双方在二十分钟里互相猜对方的底线。",
            "被拒不是你不行，是他们在找一个更便宜的你。",
            "投了没回音，是今年最常见的礼貌。",
        ],
        "translate": [
            "不是你不够好，是筛简历的人一共看了六秒。六秒看不出好坏，只看出关键词。",
            "面试挂了九成跟能力无关，跟「像不像他们想象中的人」有关。",
            "市场差的时候，个人努力的解释力会被压到很低。这不是安慰，是事实。",
        ],
        "land": [
            "今晚把简历第一屏改一遍：把「负责」换成「做成了什么，数字是多少」。",
            "明天只投三家，但每家的岗位描述抄一遍，把里面的词换进简历。",
            "被拒之后记一行：哪一步卡住。三行之后你会看见规律。",
        ],
    },
    "marriage": {
        "hook": [
            "「为你好」这三个字，是中文里最难反驳的一种攻击。",
            "催婚的本质不是关心你的婚姻，是缓解他们自己的不确定。",
            "他们催的不是你结婚，是他们那套时间表还没作废。",
        ],
        "translate": [
            "「没人要」是她那代人的货币，不是你的定价。",
            "他们要的是安心，你要的是安静。这两件事不冲突，只是没人说破。",
            "你不是不孝，你只是不肯用自己的人生去交他们的作业。",
        ],
        "land": [
            "下次就回一句：我在挑，挑不好不如不挑。然后去洗碗。",
            "把话题从「结不结」换成「我今年打算怎么过」。具体的计划比立场好用。",
            "给自己划一条线：哪些问题不答。被问就笑一下，去厨房帮忙。",
        ],
    },
    "tired": {
        "hook": [
            "累是身体在替你开会，议题是「能不能别干了」。",
            "你不是懒，你是电量到 5% 还在跑大型程序。",
            "这种累不是睡一觉能解决的，是睡十觉也还剩一半的那种。",
        ],
        "translate": [
            "持续性的疲惫，通常是「做了很多、但没一件是自己的」造成的。",
            "不是你抗压能力差，是你已经压了太久，麻了。",
            "身体比脑子诚实。它先罢工，是因为它不会讲道理。",
        ],
        "land": [
            "今晚什么都别解决。洗个热水澡，把手机放远一点，睡。",
            "明天请半天假，什么都不干。不是奖励，是维修。",
            "这周找一件完全没用的事做一次：散步、拼图、看烂片。没用的事最补人。",
        ],
    },
    "money": {
        "hook": [
            "工资这个东西，涨的时候是恩情，不涨的时候是行情。",
            "月光不是不会理财，是理了也不剩。",
            "存款的意义不是变富，是让你在被欺负的时候能说不。",
        ],
        "translate": [
            "你存不下钱，多半不是消费问题，是收入结构问题。这两个的解法完全不同。",
            "穷最难受的地方不是买不起，是每次都要先算一遍。",
            "钱少的时候，省钱的天花板很低，涨收入的天花板高得多。",
        ],
        "land": [
            "今晚只做一件事：把这个月固定支出列出来，看哪个能砍。一个就够。",
            "明天问一句：这个岗位的市场价是多少。不问永远不会涨。",
            "先存一笔「滚蛋基金」，哪怕五百。它的作用是让你敢说话。",
        ],
    },
    "colleague": {
        "hook": [
            "甩锅这件事，考验的不是人品，是留痕意识。",
            "职场里的委屈，八成来自「没写下来」。",
            "背锅的人往往不是最弱的，是最不爱解释的。",
        ],
        "translate": [
            "他不是针对你，他是针对所有人，这叫公平。",
            "会哭的孩子有奶吃，不是因为他委屈，是因为他让别人知道了。",
            "你把活干了但不留痕，等于把功劳和责任的归属权一起交出去了。",
        ],
        "land": [
            "以后所有口头安排，回一句「我确认一下：A 我做，B 你做，对吗」。这句能救命。",
            "把这次的时间线写成五行，自己留着。不用发，先有。",
            "下次分工时，主动说「这块我负责到哪一步」，把边界说在前面。",
        ],
    },
    "breakup": {
        "hook": [
            "分手最难受的不是失去，是突然没人可交代了。",
            "失恋是一场戒断，不是你不够好。",
            "他现在过得怎么样，跟你没关系。这句话你要念三遍。",
        ],
        "translate": [
            "你怀念的可能不是他，是有人等你回家的那种结构。",
            "反复想「如果当时我……」，是大脑在找一个能控制的原因。没有。",
            "难过是正常的，它不需要被优化，也不需要被解释。",
        ],
        "land": [
            "今晚把那几张照片移到一个不常翻的文件夹。不删，只挪。",
            "明天约一个朋友吃饭，只吃饭，不聊这件事。",
            "给自己两周：这两周不做任何关于他的决定。",
        ],
    },
    "selfworth": {
        "hook": [
            "「没用」这个词，你用得比谁都狠。",
            "你对自己的评价，比你实际的表现严格得多。",
            "这句我不接。",
        ],
        "translate": [
            "你不是没用，你是连着累了很久之后，用一句最省力的话骂自己。",
            "把「我什么都做不好」拆开看，通常只有一两件事真的没做好，其余是被这句话带走的。",
            "人在低电量的时候，对自己的判断力也会跟着掉。",
        ],
        "land": [
            "今晚先睡。明天找一个具体的人说一句实话，别在深夜给自己下结论。",
            "写三件今天做成的小事，多小都算。不是为了正能量，是为了校准。",
            "明天做一件五分钟能做完的事，做完划掉。用完成感打断这个循环。",
        ],
    },
    "default": {
        "hook": [
            "这事听起来不像大事，但显然已经在你脑子里转了很久。",
            "先说结论：不是你太敏感。",
            "你说的这个，我大概能懂那种钝钝的难受。",
        ],
        "translate": [
            "很多事难受，不是因为结果坏，是因为它一直没被说清楚。",
            "你缺的可能不是办法，是一个能接住这句话的人。",
            "想不通的时候，往往是问题本身问错了。",
        ],
        "land": [
            "今晚先别下结论。睡一觉，明天再看，很多事会自己缩小。",
            "把它写成三行：发生了什么、我在意什么、我明天能做什么。写完就放下。",
            "找一个具体的人说一遍。说出来和想，是两件不同的事。",
        ],
    },
}

_L2_TEMPLATES = {
    "hook": ["这句我不接梗。", "我不打算把这句话翻译成道理。", "先停一下，这句值得认真回。"],
    "translate": [
        "你现在不是需要分析，是需要有人在旁边。",
        "能说出这句话，说明你已经撑了很久，而不是你脆弱。",
        "这种状态不是性格问题，是消耗太久之后的正常反应。",
    ],
    "land": [
        "今天先别给自己下结论。找个具体的人说一句实话，不用说得漂亮，说「我最近不太好」就够。",
        "如果身边暂时没人，打 12356，24 小时有人接。这不是小题大做。",
        "今晚只做一件事：让自己待在有人的地方。别的明天再说。",
    ],
}

_DEFAULT_TOPIC = "default"

# 「更损 / 更暖」两档的通用替换句。取向与 persona 一致：
# 损档只损处境和系统，永不动用户；暖档去掉嘲讽但不许变成空口安慰。
_SPICIER_TRANSLATE = [
    "真正离谱的是，一个连标准都写不出来的评价，居然能当结论用。",
    "这事的荒诞在于：他被要求有品位，你被要求猜他的品位。",
    "问题不在你的方案，在于这套流程把「我说不好」当成了「你做得差」。",
]

_WARMER_HOOKS = [
    "被当众这么说，确实挺难接的。",
    "这话当面听下来，谁都得不舒服一会儿。",
    "这种时候心里堵一下，很正常。",
]

_WARMER_TRANSLATE = [
    "你不是做得差，是没人把标准说清楚。",
    "难受是因为你在意，不是因为你不堪。",
    "你已经在很认真地对待这件事了，这本身不是缺点。",
]


def _pick(options: list[str], seed: str) -> str:
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def _detect_topic(text: str) -> str:
    for topic, kws in _TOPICS:
        if any(k in text for k in kws):
            return topic
    return _DEFAULT_TOPIC


def _clean(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^[\s「」\"'`]+|[\s「」\"'`]+$", "", t)
    return t


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

        level_match = re.search(r"本轮到\s*(L[0-3])\s*级", system)
        level = level_match.group(1) if level_match else "L0"

        # 「更损 / 更暖」在真模型里是 prompt 指令；mock 没有理解力，
        # 必须自己读指令改选词，否则默认 provider 下两个按钮点了没反应。
        variant = "default"
        if "损度提高" in system:
            variant = "spicier"
        elif "暖度提高" in system:
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
