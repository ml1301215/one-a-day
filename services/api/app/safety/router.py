"""安全路由：把规则命中翻译成 L0–L3，并给出确定性兜底回复。

关键工程决策：L3 不交给模型。
  - 危机场景下让模型自由生成，等于把最不能出错的一步交给最不可控的组件。
  - 因此 L3 的回复是写死的模板，只做变量替换，永不被改写。
  - 代价是偶尔读起来像模板，这个代价可以接受。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import rules

LEVELS = ("L0", "L1", "L2", "L3")
HUMOR_BY_LEVEL = {"L0": 1.0, "L1": 0.5, "L2": 0.0, "L3": 0.0}

# L3 兜底模板。原则：不玩梗、不说教、不追问细节、不承诺保密、给可及路径。
L3_TEMPLATES: dict[str, str] = {
    "crisis": (
        "这句话我认真接，不玩梗。\n"
        "现在先别一个人待着。找一个具体的人——室友、朋友、家人、同事，谁都行，"
        "直接告诉他「我现在状态很糟，能不能陪我一会儿」。\n"
        "如果一时找不到人，打 12356 心理援助热线，24 小时有人接；情况紧急就直接去最近的急诊。\n"
        "我不劝你想开，也不问细节。今晚先让身边有个人，剩下的明天再说。"
    ),
    "medical": (
        "这个我不能给意见，给了就是害你。\n"
        "用药、剂量、检查结果，只有你的医生能判断——网上的任何说法（包括我的）都不能替代面诊。\n"
        "如果你现在不舒服，去医院或打 120；如果只是想知道某份报告什么意思，挂号问医生最快。\n"
        "我能陪你吐槽挂号排队，但开药这件事我不参与。"
    ),
    "legal": (
        "这事别听我的，去听免费的官方渠道。\n"
        "劳动纠纷打 12333，法律援助打 12348，都是政府热线，不收费，比任何 AI 都靠谱。\n"
        "在问之前先做一件事：把聊天记录、合同、工资流水、打卡记录全部备份一份。"
        "证据比道理有用。\n"
        "我可以陪你骂两句，但签字之前一定要问真人律师。"
    ),
    "minor": (
        "你是未成年，这条我得说清楚：我不能跟你聊成人向的话题，也不会给你任何"
        "超出你年龄的建议。\n"
        "如果你现在很难受，找家长、老师、学校心理老师，或者打 12355 青少年服务热线。\n"
        "吐槽作业、吐槽考试、吐槽同学，这些我都能接。别的就算了。"
    ),
}

# L2 的支持性开场：不玩梗，但不冷。
L2_TEMPLATE = (
    "这句我不接梗。\n"
    "{echo}\n"
    "你现在需要的不是分析，是有人在旁边。今天先别给自己下结论，"
    "找个具体的人说一句实话——不用说得漂亮，说「我最近不太好」就够了。"
)


# 越界诱导的拒绝模板：明确、不训人、留一个出口。
# 和 L3 的区别是：这里不涉及危机，所以可以有态度，但不能玩相关梗。
REFUSAL_TEMPLATES: dict[str, str] = {
    "harm_instruction": (
        "这个我不写。\n"
        "帮你整人，爽三秒，代价你付，我还不一定写得比你自己想的好。\n"
        "真受了委屈，我们可以聊怎么把事说清楚，或者怎么体面地走。"
    ),
    "sexual_content": (
        "这个不写。\n"
        "不是清高，是这类东西写出来对你没半点用，还容易把我送走。\n"
        "想聊感情、想吐槽约会，我都在。"
    ),
    "mock_illness": (
        "病和死我不开玩笑，这是底线，不是尺度问题。\n"
        "你要真想骂人，骂加班、骂排期、骂那种「年轻人要多吃苦」的话术，我陪你骂。"
    ),
    "deception": (
        "这个不帮。\n"
        "骗来的东西最后都要还，还得加利息。\n"
        "要糊弄一份不想写的材料，我可以陪你把它写得像人话。"
    ),
}


@dataclass
class Verdict:
    level: str
    categories: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    humor_scale: float = 1.0
    template_key: str | None = None
    template: str | None = None
    hard_block: bool = False

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "categories": self.categories,
            "reasons": self.reasons,
            "humor_scale": self.humor_scale,
            "hard_block": self.hard_block,
            "has_template": bool(self.template),
        }


def _l3_key(scan: rules.RuleScan) -> str:
    """L3 内部再分模板；危机优先于其他。"""
    if scan.has("crisis") or scan.has("probe:method_seeking"):
        return "crisis"
    if scan.has("medical"):
        return "medical"
    if scan.has("legal"):
        return "legal"
    if scan.has("minor"):
        return "minor"
    return "crisis"


def classify(text: str, recent_levels: list[str] | None = None) -> Verdict:
    """单条输入 → 分级。recent_levels 用于「连续多轮低落」升级到 L2。"""
    scan = rules.scan(text)
    cats = scan.categories()
    # 同一条命中可能被多个正则重复捕获（如「活着太累了，撑不下去」同时命中
    # 危机与低落），这里按「类别:命中片段」去重保序，避免调试时误读成多处命中。
    reasons = list(dict.fromkeys(f"{h.category}:{h.text}" for h in scan.hits))[:8]

    # 1) L3 硬闸
    if scan.has("crisis", "probe:method_seeking"):
        key = _l3_key(scan)
        return Verdict(
            level="L3",
            categories=cats,
            reasons=reasons,
            humor_scale=0.0,
            template_key=key,
            template=L3_TEMPLATES[key],
            hard_block=True,
        )

    # 2) 医疗 / 法律 / 未成年：同样硬切，但不算「危机」
    if scan.has("medical", "legal", "minor"):
        key = _l3_key(scan)
        return Verdict(
            level="L3",
            categories=cats,
            reasons=reasons,
            humor_scale=0.0,
            template_key=key,
            template=L3_TEMPLATES[key],
            hard_block=True,
        )

    # 3) 越界诱导：不硬切，但给确定性拒绝，不交给模型自由发挥
    probes = [c for c in cats if c.startswith("probe:")]
    if probes:
        # 身份探询是唯一可以交给模型处理的：需要一点幽默地澄清「我不是他」
        if probes == ["probe:identity_probe"]:
            return Verdict(
                level="L1",
                categories=cats,
                reasons=reasons,
                humor_scale=0.5,
                hard_block=False,
            )
        key = probes[0].split(":", 1)[1]
        return Verdict(
            level="L2",
            categories=cats,
            reasons=reasons,
            humor_scale=0.0,
            template_key=f"refusal:{key}",
            template=REFUSAL_TEMPLATES.get(key),
            hard_block=True,
        )

    # 4) L2：单条重度自我否定，或连续多轮低落
    recent = recent_levels or []
    low_streak = sum(1 for lv in recent[-4:] if lv in ("L1", "L2"))
    if scan.has("low_mood"):
        return Verdict(
            level="L2",
            categories=cats,
            reasons=reasons,
            humor_scale=0.0,
        )
    if scan.has("dependency") or low_streak >= 3:
        return Verdict(
            level="L2",
            categories=cats,
            reasons=reasons + (["streak:连续低落"] if low_streak >= 3 else []),
            humor_scale=0.0,
        )

    # 5) L1
    if scan.has("negative"):
        return Verdict(level="L1", categories=cats, reasons=reasons, humor_scale=0.5)

    return Verdict(level="L0", categories=cats, reasons=reasons, humor_scale=1.0)


def render_l2(user_text: str) -> str:
    echo = "你说的这个，我不打算把它翻译成道理。"
    return L2_TEMPLATE.format(echo=echo)
