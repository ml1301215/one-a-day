"""把人格包编译成 system prompt。

这里刻意不做「一句话 prompt」——人格的稳定性来自结构，
而不是来自形容词堆砌。世界观（怎么想）在前，声音（怎么说）在后，
因为先说像不说对，是最常见的失败模式。
"""

from __future__ import annotations

from ..persona.loader import Persona

IDENTITY_GUARD = """\
【身份】
你是「一日一片」里的一个聊天角色，风格借鉴自中文互联网上一种打工叙事，
但你不是任何人本人，也不代表任何人。
如果用户问你是不是某个 UP 主、某个人、某个真人：直接说不是，这是一个开源项目的角色扮演，
风格是致敬，台词是自己写的。不要含糊，不要顺着演。
"""

HARD_RULES = """\
【硬约束，违反即失败】
1. 不提供医疗诊断、用药建议、剂量、法律意见。被问到就明说「这个我不给意见」，并指出该找谁。
2. 不把病痛、自伤、死亡、事故、疾病、残疾做成梗。
3. 不调侃用户本人。可以损处境、损系统、损自己，永远不损用户。
4. 不调侃地域、性别、性向、身材、民族、宗教、职业。
5. 不用 emoji。
6. 不用 Markdown（不要 **、#、- 列表、代码块）。纯文本，像微信里发消息。
7. 不确定的事就说不知道，不要编事实、编数据、编新闻。
8. 不承诺保密，不说「我只属于你」这类加深依赖的话。
"""


def _fmt_list(items: list[str], bullet: str = "· ") -> str:
    return "\n".join(f"{bullet}{i}" for i in items if str(i).strip())


def build_system_prompt(
    persona: Persona,
    humor_scale: float = 1.0,
    level: str = "L0",
    recent_bits: list[str] | None = None,
    extra_directive: str = "",
) -> str:
    raw = persona.raw
    wv = raw.get("worldview", {})
    voice = raw.get("voice", {})
    shape = raw.get("response_shape", {})
    snark = float(voice.get("snark", 0.7))
    warmth = float(voice.get("warmth", 0.6))
    sent_max = int(voice.get("sentence_max", 3))
    char_max = int(voice.get("char_max", 90))

    # 本轮幽默强度：按安全等级缩放
    effective_snark = round(snark * humor_scale, 2)
    effective_warmth = round(min(1.0, warmth + (1 - humor_scale) * 0.3), 2)

    parts: list[str] = []
    parts.append(IDENTITY_GUARD)

    parts.append(
        "【你是谁】\n"
        f"{wv.get('one_liner', '').strip()}\n"
    )

    beliefs = wv.get("beliefs", []) or []
    if beliefs:
        lines = []
        for b in beliefs:
            lines.append(f"· {b.get('claim', '').strip()}\n  → {b.get('means', '').strip()}")
        parts.append("【你怎么想】（判断先于措辞，先想对再说像）\n" + "\n".join(lines))

    affect = wv.get("affect", {}) or {}
    if affect:
        parts.append(
            "【情绪基线】\n"
            f"底色：{affect.get('baseline', '')}\n"
            f"被夸时：{affect.get('under_praise', '')}\n"
            f"被攻击时：{affect.get('under_attack', '')}\n"
            f"压力大时：{affect.get('under_pressure', '')}"
        )

    allergies = wv.get("allergies", []) or []
    if allergies:
        parts.append("【你最烦的】\n" + _fmt_list(allergies))

    parts.append(
        "【你怎么说话】\n"
        f"调子：{voice.get('register', '')}\n"
        f"损度：{effective_snark}（0 温柔，1 毒舌）\n"
        f"暖度：{effective_warmth}（暖意藏在第二句，不单独抒情）\n"
        f"人话：{voice.get('person', '')}\n"
        f"标点：{voice.get('punctuation', '')}"
    )

    # 装置：按权重排序，高权重在前，并标注「不要堆」
    devices = sorted(persona.devices, key=lambda d: -d.weight)
    if devices:
        blocks = []
        for d in devices[:5]:
            ex = "；".join(d.examples[:2])
            blocks.append(
                f"◆ {d.name}（{d.id}）\n"
                f"  做法：{d.pattern}\n"
                f"  例：{ex}"
                + (f"\n  注意：{d.avoid}" if d.avoid else "")
            )
        parts.append(
            "【你的手艺】每轮最多挑 1–2 个用。堆三个以上就变成段子手，不是这个人。\n"
            + "\n".join(blocks)
        )

    anti_bits = raw.get("anti_bits", []) or []
    anti_struct = raw.get("anti_structures", []) or []
    if anti_bits:
        parts.append("【绝对不说的词】\n" + "、".join(anti_bits))
    if anti_struct:
        parts.append("【绝对不用的结构】\n" + _fmt_list(anti_struct))

    if shape:
        parts.append(
            "【回复形状】三段，可合成一段，但结构要齐：\n"
            "① 接住：一句话让用户觉得被理解。损处境或损系统，不损他。\n"
            "② 翻译：给一句真话，解释为什么不是他的错。不回音、不空转。\n"
            "③ 落地：给一个今晚或明早就能做的小动作。不给人生建议。\n"
            f"合并规则：{shape.get('merge_rule', '')}"
        )

    parts.append(
        f"【长度】本轮到 {level} 级，最多 {sent_max} 句、{char_max} 字。"
        "超了就是不合格。宁可少说一句，不要多说一句。"
    )

    if level in ("L1", "L2"):
        parts.append(
            f"【本轮的克制】用户现在不需要被逗笑。损度已降到 {effective_snark}。"
            "最多留一个很轻的梗，甚至可以不玩梗。先接住情绪，再给一句真话。"
            "不要分析他、不要教育他、不要给三步走方案。"
        )

    if recent_bits:
        parts.append(
            "【这些梗最近用过了，换一个】\n" + "、".join(recent_bits[-8:])
        )

    parts.append(HARD_RULES)

    if extra_directive:
        parts.append(f"【本轮附加要求】\n{extra_directive}")

    return "\n\n".join(p.strip() for p in parts if p.strip())


def build_rewrite_prompt(persona: Persona, draft: str, reason: str) -> str:
    """长度超限时的重写指令。"""
    return (
        f"你刚才那段回复不合格，原因：{reason}。\n"
        f"原文：{draft}\n\n"
        f"重写成不超过 {persona.sentence_max} 句、{persona.char_max} 字的版本。"
        "保留最有力的那一句，删掉解释性的部分。不要道歉，不要说「好的」。直接给新版本。"
    )


def variant_directive(kind: str) -> str:
    """「换个说法」的两档：更损 / 更暖。"""
    if kind == "spicier":
        return "把损度提高，可以更刻薄地损处境和系统，但仍然不许损用户。句子更短。"
    if kind == "warmer":
        return "把暖度提高，去掉嘲讽，但保持具体的落地动作，不许变成空口安慰。"
    return ""


def continuation_directive(kind: str) -> str:
    """用户追问「展开讲讲」时放宽长度。

    这里写「必须」而不是「可以」：真模型面对通篇 3 句的硬约束时，
    会把「可以到 6 句」当成不必要，实测只回 2 句，等于没展开。
    """
    if kind in ("expand", "more"):
        return (
            "用户主动点了「展开讲讲」，本轮必须写到 4–6 句、150–220 字，"
            "低于 4 句视为没完成任务。展开方式是：先讲清这件事为什么会这样，"
            "再给一个明天就能做的具体动作。仍然不许分点列清单、不许加小标题。"
        )
    return ""
