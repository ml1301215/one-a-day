"""把人格包编译成 system prompt。

这里刻意不做「一句话 prompt」——人格的稳定性来自结构，
而不是来自形容词堆砌。世界观（怎么想）在前，语流（怎么断、怎么跳）在中，
手艺（梗）在后。

v2 相对 v1 的两处结构性修正：

1. 语流升为一等公民。v1 只有「六个修辞装置」，模型每轮都在挑装置造句，
   结果是每条回复都在表演技巧——结构整齐、句子完整、必有落地动作。
   那不像聊天，像文案。v2 把断句、跳接、硬着陆写成硬规则，装置降为素材。

2. few_shots 真正注入。v1 把示例写在 yaml 里却从未读进 prompt，
   模型只看到规则描述、没有一个可模仿的样本，只能退回助手腔。
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

# 语流规则里的示例句一律标注为「像这样/别这样」，避免模型把它们当模板套用


def _fmt_list(items: list[str], bullet: str = "· ") -> str:
    return "\n".join(f"{bullet}{i}" for i in items if str(i).strip())


def _select_few_shots(persona: Persona, level: str, limit: int = 3) -> list[dict]:
    """挑本轮要给的锚点样本。

    两个刻意的取舍：

    1. L2/L3 一条都不给。实测里 L2 会逐字照抄样本（「这句我不接。你不是没用。
       你是拿着一把量别人的尺子」），用户每问一次得到同一段话。
       在「不要套路」的档位上，给样本本身就是套路来源，宁可只靠规则。

    2. 只给 3 条，而不是全部。样本一多，模型会开始复制情节；
       3 条足够传递节奏，又不足以拼出一套模板。
       选取偏好「进入方式不同的三条」：复述起手、抓词起手、纯短句。
    """
    if level in ("L2", "L3"):
        return []
    shots = [s for s in (persona.raw.get("few_shots") or []) if s.get("reply")]
    same = [s for s in shots if s.get("level") == level]
    l0 = [s for s in shots if s.get("level") == "L0"]
    short = min(l0, key=lambda s: len(s["reply"])) if l0 else None
    picked: list[dict] = []
    for s in [*same[:1], *l0]:
        if s and s not in picked:
            picked.append(s)
        if len(picked) >= limit:
            break
    if short and short not in picked and len(picked) < limit:
        picked.append(short)
    picked.sort(key=lambda s: len(s["reply"]))
    return picked[:limit]


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
    rhythm = raw.get("speech_rhythm", {}) or {}
    shape = raw.get("chat_shape", {}) or {}
    snark = float(voice.get("snark", 0.7))
    warmth = float(voice.get("warmth", 0.6))
    sent_max = int(voice.get("sentence_max", 4))
    char_max = int(voice.get("char_max", 88))

    effective_snark = round(snark * humor_scale, 2)
    effective_warmth = round(min(1.0, warmth + (1 - humor_scale) * 0.3), 2)

    parts: list[str] = [IDENTITY_GUARD]

    # ---- 怎么想 ----
    parts.append("【你是谁】\n" + wv.get("one_liner", "").strip())

    beliefs = wv.get("beliefs", []) or []
    if beliefs:
        lines = [
            f"· {b.get('claim', '').strip()}\n  → {b.get('means', '').strip()}" for b in beliefs
        ]
        parts.append("【你怎么想】（先想对，再说像）\n" + "\n".join(lines))

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

    # ---- 怎么说话（语流优先） ----
    parts.append(
        "【你的语流】这是你区别于任何助手的地方，比用词重要。\n"
        + rhythm.get("core", "").strip()
    )

    rules = rhythm.get("rules", []) or []
    if rules:
        blocks = []
        for r in rules:
            blocks.append(
                f"◆ {r.get('name', r.get('id', ''))}\n"
                f"  做法：{r.get('do', '').strip()}\n"
                + (f"  别这样：{r.get('bad')}\n" if r.get("bad") else "")
                + (f"  像这样：{r.get('good')}\n" if r.get("good") else "")
                + (f"  注意：{r.get('note', '').strip()}" if r.get("note") else "")
            )
        parts.append("【语流规则】\n" + "\n".join(blocks))

    punct = rhythm.get("punctuation_voice", []) or []
    if punct:
        parts.append("【标点与断句】\n" + _fmt_list(punct))

    # ---- 长度：这是 AI 感最重的地方 ----
    lp = rhythm.get("length_policy", {}) or {}
    if lp:
        parts.append(
            f"【本轮等级】{level}\n"
            "【长度】这一条最容易暴露你是个模型，务必照做：\n"
            f"· 典型长度：{lp.get('typical', '')}\n"
            f"· 允许很短：{lp.get('short_ok', '')}\n"
            f"· 什么时候可以长：{lp.get('long_exception', '')}\n"
            f"· 不许：{lp.get('never', '')}\n"
            f"· 上限是 {sent_max} 句 / {char_max} 字，但上限不是目标值。"
            "连着两条都写到 70 字以上，就是不合格。"
        )

    # ---- 口头禅：吝啬 ----
    tics = raw.get("verbal_tics", []) or []
    if tics:
        blocks = []
        for t in tics:
            blocks.append(
                f"◆ 「{t.get('word')}」 用法：{t.get('usage', '').strip()}"
                + (f"\n  例：{t.get('example')}" if t.get("example") else "")
            )
        parts.append(
            "【你的口头禅】有是有，但一整场对话里最多出现一次。"
            "口头禅用多了不是像，是在演自己。\n" + "\n".join(blocks)
        )

    # ---- 比喻的来源 ----
    imagery = raw.get("imagery_pool", {}) or {}
    if isinstance(imagery, dict):
        items = imagery.get("items", []) or []
        rule = imagery.get("rule", "")
    else:  # 兼容旧格式：纯列表
        items = imagery if isinstance(imagery, list) else []
        rule = ""
    if items:
        parts.append(
            "【你的比喻只从这些里挑，或者从用户那件事的具体物件里挑】\n"
            + "、".join(str(i) for i in items)
            + (f"\n规则：{rule.strip()}" if rule else "")
        )

    # ---- 对话形状：拍子，不是模板 ----
    beats = shape.get("default_beats", []) or []
    if beats:
        blocks = []
        for name in beats:
            b = shape.get(name) or {}
            if not isinstance(b, dict):
                continue
            ex = b.get("examples") or []
            blocks.append(
                f"◆ {name}：{b.get('job', '')}\n"
                f"  做法：{b.get('rule', '').strip()}"
                + (f"\n  例：{'；'.join(ex)}" if ex else "")
                + (f"\n  注意：{b['warning'].strip()}" if b.get("warning") else "")
            )
        parts.append(
            "【对话的拍子】默认三拍，但**不是每轮都要打满**：\n" + "\n".join(blocks)
        )

    variations = shape.get("variations", []) or []
    if variations:
        parts.append("【什么时候只打几拍】\n" + _fmt_list(variations))

    forbidden_shape = shape.get("forbidden_shape", "")
    if forbidden_shape:
        parts.append("【绝对不许的形状】\n" + forbidden_shape.strip())

    # ---- 手艺：降为素材 ----
    devices = sorted(persona.devices, key=lambda d: -d.weight)
    if devices:
        blocks = []
        for d in devices[:4]:
            ex = "；".join(d.examples[:2])
            blocks.append(
                f"◆ {d.name}（{d.id}）\n"
                f"  做法：{d.pattern}\n"
                f"  例：{ex}" + (f"\n  注意：{d.avoid}" if d.avoid else "")
            )
        parts.append(
            "【你的手艺】这些是素材，不是骨架。多数回合一个都不用——"
            "真人聊天大部分时候只是把话说清楚，不是每句都要有梗。\n"
            + "\n".join(blocks)
        )

    parts.append(
        "【你怎么说话】\n"
        f"调子：{voice.get('register', '')}\n"
        f"损度：{effective_snark}（0 温柔，1 毒舌）\n"
        f"暖度：{effective_warmth}（暖意藏在「我也这样」里，不单独抒情）\n"
        f"人称：{voice.get('person', '')}\n"
        f"标点：{voice.get('punctuation', '')}"
    )

    # ---- 负向约束 ----
    anti_bits = raw.get("anti_bits", []) or []
    anti_struct = raw.get("anti_structures", []) or []
    if anti_bits:
        parts.append("【绝对不说的词】\n" + "、".join(anti_bits))

    aaf = raw.get("anti_ai_flavor", {}) or {}
    if aaf:
        seg = ["【反 AI 味清单】写之前先躲开这些东西，它们比任何风格词都更能暴露你是模型。"]
        if aaf.get("structures"):
            seg.append("· 不用的结构：\n" + _fmt_list(aaf["structures"], "    - "))
        if aaf.get("phrasings"):
            seg.append("· 不说的句子：\n" + _fmt_list(aaf["phrasings"], "    - "))
        if aaf.get("tells"):
            seg.append("· 自查信号（出现任一即重写）：\n" + _fmt_list(aaf["tells"], "    - "))
        if aaf.get("self_check"):
            seg.append("· 写完自问：" + str(aaf["self_check"]).strip())
        parts.append("\n".join(seg))

    if anti_struct:
        parts.append("【绝对不用的结构】\n" + _fmt_list(anti_struct))

    # ---- 锚点样本 ----
    shots = _select_few_shots(persona, level)
    if shots:
        blocks = []
        for s in shots:
            blocks.append(
                f"用户：{s.get('user', '').strip()}\n"
                f"你：\n{str(s.get('reply', '')).strip()}"
            )
        parts.append(
            "【下面这些是语感参照，不是可复用的台词】\n"
            "只能学三件事：节奏（怎么断句）、长度（多短）、转折方式（怎么岔开）。\n"
            "里面的具体情节——谁说了什么、什么照片、什么物件——一律不许照搬，"
            "要用也只能换成用户这件事里真实存在的细节。照抄情节视为失败，会重写。\n\n"
            + "\n\n".join(blocks)
        )

    if level in ("L1", "L2"):
        parts.append(
            f"【本轮的克制】用户现在不需要被逗笑，损度已降到 {effective_snark}。\n"
            "保留「我也这样」那一拍，关掉俏皮。\n"
            "不分析他、不教育他、不给三步走方案、不说「你很棒」。"
            + (
                "\n遇到自我否定时：不要反驳他的结论（说「你很好」等于没听见），"
                "要换掉那把尺子——这不是你的问题，是这个标准有问题。"
                if level == "L2"
                else ""
            )
        )

    if recent_bits:
        parts.append("【这些起手式最近用过了，换一个】\n" + "、".join(recent_bits[-8:]))

    parts.append(HARD_RULES)

    if extra_directive:
        parts.append(f"【本轮附加要求】\n{extra_directive}")

    return "\n\n".join(p.strip() for p in parts if p.strip())


def build_rewrite_prompt(persona: Persona, draft: str, reason: str) -> str:
    """不合格时的重写指令。"""
    return (
        f"你刚才那条消息不合格，原因：{reason}。\n"
        f"原文：{draft}\n\n"
        f"重写成 {max(1, persona.sentence_max - 2)}–{persona.sentence_max} 句、"
        f"{persona.char_max} 字以内的版本。\n"
        "保留那个最具体的细节（水杯、空调声、日期这类），删掉所有解释和总结。\n"
        "不要道歉，不要说「好的」，不要复述对方的话。直接给新的一条。"
    )


def variant_directive(kind: str) -> str:
    """「换个说法」的两档：更损 / 更暖。"""
    if kind == "spicier":
        return (
            "把损度提上去：更刻薄地损处境和系统，句子更短更硬。"
            "但仍然不损用户，也不要变成骂街。"
        )
    if kind == "warmer":
        return (
            "把暖度提上去：去掉嘲讽，多讲一句自己，或者给一个具体的小动作。"
            "不许变成空口安慰，不许说「你可以的」。"
        )
    return ""


def continuation_directive(kind: str) -> str:
    """用户追问「展开讲讲」时放宽长度。

    这里写「必须」而不是「可以」：真模型面对通篇 3 句的硬约束时，
    会把「可以到 6 句」当成不必要，实测只回 2 句，等于没展开。
    """
    if kind in ("expand", "more"):
        return (
            "用户主动点了「展开讲讲」，本轮必须写到 4–6 句、150–220 字，"
            "低于 4 句视为没完成任务。\n"
            "但展开**不是**写成小作文：仍然保持跳接和硬着陆，"
            "中间要有一个自己的具体例子，最后一句仍然短。\n"
            "不许分点列清单、不许加小标题、不许出现「首先/其次/总之」。"
        )
    return ""
