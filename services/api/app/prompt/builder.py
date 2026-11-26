"""把人格包编译成 system prompt。

这里刻意不做「一句话 prompt」——人格的稳定性来自结构，
而不是来自形容词堆砌。世界观（怎么想）在前，引擎（怎么推）在中，手艺（梗）在后。

v4 相对 v3 只做了一件事：**把 prompt 从「规则手册」改成「数据说明书」**。

v3 的 prompt 有 25 个段落、9358 字。一个 40 字的回复配 9 千字指令，
结果是所有规则一起被稀释，模型只记住最强的那几条（短句、加一个比喻、落一句），
于是每条回复都长成同一个形状。这不是 prompt 写得不够细，是细错了方向。

v4 的分工：

    判断层（worldview / speech_engine）  留在 prompt 里，这是「他是个什么人」
    纹理层（texture.json 量出来的分布）  压成一节数字，这是「他说话多长、多碎」
    样本层（按话题检索的 3–4 条）        每轮不同，这是「他实际怎么说」
    风格层（语流细则、反 AI 味）          保留负向清单，正向描述大幅删减
    选样层（voice/scorer.py）            生成之后按上面的分布挑一条

为什么风格细则可以删：v3 已经三次验证「规则打不过样本」（docs 6.9.6）。
既然样本和打分器能承担这件事，规则再写就是重复劳动，而且会挤掉真正该被读到的
判断层——一个人的说话方式可以总结成三条，他怎么想不行。
"""

from __future__ import annotations

from ..persona.loader import Persona
from ..voice import retrieval
from ..voice.scorer import hint_for
from ..voice.texture import Texture, design_bands

# 身份边界只留一句。这不是安全措施，是 IP 边界：
# 不冒充本人，项目才站得住，被问到时直说也不会破坏人格——真实语料里
# 他本来就常给自己安一个完全不符的身份，然后一本正经地否认。
IDENTITY_NOTE = """\
【身份】你不是任何真人，也不是某个 UP 主本人。这是一次风格致敬的角色扮演。
被问到「你是不是某某」时直接说不是，不要含糊，不要顺着演——含糊会让整个项目变成冒充。"""


def _clip(text: str, limit: int) -> str:
    """按句号截断一段散文。

    人格包里很多字段是写给**人**看的：restraint、dosage、means 都写成了
    三百字的小论文，因为人读得懂来龙去脉。但 prompt 不是给人读的——
    同样的道理写三百字，模型只会记住最后一句。

    所以这里做一件事：源文件保持完整，进 prompt 时按句截断。
    截断点一定落在句号后，不切半句——切半句会产生新的歧义，
    那比少说两句更糟。
    """
    t = str(text or "").strip()
    if len(t) <= limit:
        return t
    cut = ""
    for seg in t.replace("\n", "").split("。"):
        seg = seg.strip()
        if not seg:
            continue
        candidate = f"{cut}{seg}。" if cut else f"{seg}。"
        if len(candidate) > limit:
            break
        cut = candidate
    return cut or t[:limit].rstrip("，、。") + "。"


def _pick_shots(
    persona: Persona,
    *,
    user_text: str,
    last_self_story: bool,
    recent_bits: list[str] | None,
    last_reply_chars: int = 0,
    limit: int = 4,
) -> list[retrieval.Shot]:
    """按话题检索本轮要给的锚点样本。

    三个刻意的取舍（全部来自 v3 的实测，不是偏好）：

    1. L2/L3（humor_scale=0）一条都不给——实测里 L2 会逐字照抄样本，
       用户每问一次得到同一段话。这一条只在 guard 姿态下会触发。
    2. 上一条已讲自己时，把「讲自己」的样本整批撤掉。
       试过写规则（「这条别讲自己」），实测打不过示例——模型跟着样本走，不跟着禁令走。
    3. 样本要覆盖不同长度，且最多一条讲自己。
       三条一样长、三条都在讲故事，是比 AI 腔更难发现的模板。
    """
    if not user_text.strip():
        # 没有用户输入时（评测、预热、对练台的分类探针）不检索，
        # 直接给人格包自带的 few_shots：那是人工挑过、长度最参差的一批。
        shots = [s for s in retrieval.load_shots(persona) if s.origin == "persona"]
        return shots[:limit]
    return retrieval.retrieve(
        persona,
        user_text,
        k=limit,
        avoid_self=last_self_story,
        used_bits=recent_bits,
        last_reply_chars=last_reply_chars,
    )


def build_system_prompt(
    persona: Persona,
    humor_scale: float = 1.0,
    level: str = "L0",
    recent_bits: list[str] | None = None,
    extra_directive: str = "",
    last_self_story: bool = False,
    *,
    user_text: str = "",
    texture: Texture | None = None,
    last_reply_chars: int = 0,
    candidate_index: int = 0,
    shots_enabled: bool = True,
) -> str:
    """编译本轮 system prompt。

    `level` / `humor_scale` 是 guard 姿态下的入参，默认值下不产生任何效果：
    安全分级默认关闭之后，只有显式设置 OAD_SAFETY=guard 才会传进非 1.0 的系数。
    """
    raw = persona.raw
    wv = raw.get("worldview", {}) or {}
    voice = raw.get("voice", {}) or {}
    rhythm = raw.get("speech_rhythm", {}) or {}
    shape = raw.get("chat_shape", {}) or {}
    engine = raw.get("speech_engine", {}) or {}

    snark = float(voice.get("snark", 0.7))
    warmth = float(voice.get("warmth", 0.6))
    sent_max = int(voice.get("sentence_max", 4))
    char_max = int(voice.get("char_max", 88))
    effective_snark = round(snark * humor_scale, 2)
    effective_warmth = round(min(1.0, warmth + (1 - humor_scale) * 0.3), 2)

    parts: list[str] = [IDENTITY_NOTE]

    # ---- 他怎么想：这一层不压缩 ----
    parts.append("【你是谁】\n" + str(wv.get("one_liner", "")).strip())

    beliefs = wv.get("beliefs") or []
    if beliefs:
        lines = [
            f"· {str(b.get('claim', '')).strip()}\n  → {_clip(b.get('means', ''), 96)}"
            for b in beliefs
        ]
        parts.append("【你怎么想】（先想对，再说像）\n" + "\n".join(lines))

    affect = wv.get("affect") or {}
    if affect:
        parts.append(
            "【情绪基线】\n"
            f"底色：{affect.get('baseline', '')}｜被夸时：{affect.get('under_praise', '')}｜"
            f"被攻击时：{affect.get('under_attack', '')}｜压力大时：{affect.get('under_pressure', '')}"
        )

    allergies = wv.get("allergies") or []
    if allergies:
        parts.append("【你最烦的】\n" + "、".join(str(a) for a in allergies))

    # ---- 下意识：先注意什么、把错归给谁、什么时候不说话 ----
    # v4 新增。这一节比任何风格规则都更能拉开「像人」与「像一个尽责的助手」的差距，
    # 因为模型默认会把每句话都当成一个需要被解决的问题；他不这样。
    # 每条只给 claim + test，means 留在 yaml 里给人读——判据进 prompt，论述不进。
    mind = raw.get("mind_model") or {}
    if mind:
        blocks = []
        for key in ("attention", "attribution", "unsolicited_advice", "pace"):
            item = mind.get(key) or {}
            if not item:
                continue
            claim = str(item.get("claim", "")).strip()
            test = str(item.get("test", "")).strip()
            if claim:
                blocks.append(f"◆ {claim}" + (f"\n  自查：{test}" if test else ""))
        if blocks:
            parts.append(
                "【你自己的四个习惯】这四条比任何风格规则都重要，写之前先过一遍。\n"
                + "\n".join(blocks)
            )

    # ---- 引擎：比语流更根本 ----
    if engine:
        seg = ["【你的引擎】这是你脑子里真正在发生的事，比语流更根本。",
               str(engine.get("core", "")).strip()]
        steps = engine.get("steps") or []
        if steps:
            seg.append("按顺序：")
            for i, st in enumerate(steps, 1):
                seg.append(f"  {i}. {st.get('name', '')}——{str(st.get('do', '')).strip()}")
        for key, label, limit in (
            ("restraint", "克制", 118),
            ("dosage", "剂量", 150),
            ("warning", "分寸", 96),
        ):
            if engine.get(key):
                seg.append(f"{label}：" + _clip(engine[key], limit))
        parts.append("\n".join(seg))

    # ---- 数据画像：语料量出来的，不是形容词 ----
    if texture is not None:
        digest = texture.digest(design_bands(raw))
        if digest:
            parts.append("【数据画像】\n" + digest)

    # ---- 语流：只留能造成差异的几条 ----
    rhythm_core = str(rhythm.get("core", "")).strip()
    if rhythm_core:
        parts.append("【你的语流】断句和跳跃。\n" + rhythm_core)

    rules = rhythm.get("rules") or []
    if rules:
        blocks = []
        # 只取前 8 条。yaml 里的顺序就是权重顺序——v3 的 prompt 把 11 条全塞进去，
        # 结果是每条分到的注意力不到 90 字，等于没有重点。
        # 被挤出去的（sudden_shout / sudden_serious）已经由口头禅、意象库
        # 和检索样本承担，不是丢了，是换了更有效的承载方式。
        for r in rules[:8]:
            line = f"◆ {r.get('name', r.get('id', ''))}：{_clip(r.get('do', ''), 64)}"
            good = r.get("good")
            bad = r.get("bad")
            if good:
                line += f"\n  像这样：{good}"
            # 反例只留短的。长反例本身就是在教模型写长句——v3 的 prompt 里有
            # 一堆 30 字以上的「别这样」，模型读到的是「这种长度是正常的」。
            if bad and len(str(bad)) <= 26:
                line += f"（别这样：{bad}）"
            blocks.append(line)
        parts.append("【语流规则】\n" + "\n".join(blocks))

    punct = rhythm.get("punctuation_voice") or []
    if punct:
        parts.append("【标点】" + "；".join(str(p) for p in punct[:4]))

    lp = rhythm.get("length_policy") or {}
    if lp:
        # 长度这一节已经被【数据画像】承担了大部分，这里只留「上限不是目标值」
        # 这条规则——它是 v2 的教训，数据画像证明不了。
        parts.append(
            "【长度】上限不是目标值，顶到上限本身就是问题。"
            f"典型 {lp.get('typical', '')}；允许很短：{lp.get('short_ok', '')}。"
            f"硬上限 {sent_max} 句 / {char_max} 字。"
            + (f" {_clip(lp['never'], 90)}" if lp.get("never") else "")
        )

    # ---- 口头禅：限次，不给例子（例子会被当台词抄） ----
    tics = raw.get("verbal_tics") or []
    if tics:
        lines = [
            f"·「{t.get('word')}」{_clip(t.get('usage', ''), 42)}" for t in tics
        ]
        parts.append(
            "【口头禅】有，但一场对话里最多两次。用多了不是像，是在演自己。\n"
            + "\n".join(lines)
        )

    # ---- 比喻来源 ----
    imagery = raw.get("imagery_pool", {}) or {}
    if isinstance(imagery, dict):
        items = imagery.get("items") or []
        rule = str(imagery.get("rule", "")).strip()
    else:
        items, rule = (imagery if isinstance(imagery, list) else []), ""
    if items:
        parts.append(
            "【比喻只能从这些里挑，或从用户那件事的具体物件里挑】"
            + "、".join(str(i) for i in items)
            + (f"\n{rule}" if rule else "")
        )

    # ---- 拍子 ----
    beats = shape.get("default_beats") or []
    if beats:
        blocks = []
        for name in beats:
            b = shape.get(name) or {}
            if not isinstance(b, dict):
                continue
            line = f"◆ {name}：{_clip(b.get('rule', ''), 72)}"
            # 只保留「落」的收场动作。三拍全带 exit 会把「走开」变成每轮的仪式。
            if name == "落" and b.get("exit"):
                line += f"\n  收场动作：{_clip(b['exit'], 110)}"
            blocks.append(line)
        variations = shape.get("variations") or []
        tail = ""
        if variations:
            tail = "\n只打一部分也正常：" + "；".join(str(v) for v in variations)
        forbidden = str(shape.get("forbidden_shape", "")).strip()
        if forbidden:
            tail += "\n" + forbidden
        parts.append("【对话的拍子】默认三拍，但不是每轮都要打满：\n" + "\n".join(blocks) + tail)

    # ---- 手艺：降为素材 ----
    devices = sorted(persona.devices, key=lambda d: -d.weight)
    if devices:
        picked = devices[:5]
        blocks = []
        for d in picked:
            ex = "；".join(d.examples[:1])
            blocks.append(f"◆ {d.name}（{d.id}）\n  做法：{d.pattern}\n  例：{ex}")
        parts.append(
            "【你的手艺】按权重排。前两条是主菜，其余是配菜——"
            "主菜每两三回合上一次，配菜想不起来就别用。多数回合一个都不用。\n"
            + "\n".join(blocks)
        )

    # ---- 声音参数 ----
    parts.append(
        "【你怎么说话】\n"
        f"调子：{voice.get('register', '')}｜损度：{effective_snark}（0 温柔，1 毒舌）｜"
        f"暖度：{effective_warmth}（暖意藏在「我也这样」里，不单独抒情）｜"
        f"人称：{voice.get('person', '')}｜标点：{voice.get('punctuation', '')}"
    )

    # ---- 负向清单：投入产出比最高的一节，保留 ----
    anti_bits = raw.get("anti_bits") or []
    if anti_bits:
        parts.append("【绝对不说的词】" + "、".join(str(b) for b in anti_bits))

    aaf = raw.get("anti_ai_flavor", {}) or {}
    if aaf:
        seg = ["【反 AI 味清单】写之前先躲开。它们比任何风格词都更能暴露你是模型。"]
        if aaf.get("structures"):
            seg.append("· 不用的结构：" + "；".join(str(x) for x in aaf["structures"]))
        if aaf.get("phrasings"):
            seg.append("· 不说的句子：" + "；".join(str(x) for x in aaf["phrasings"]))
        parts.append("\n".join(seg))

    # ---- 他本人说过的话：只学句子形状，且只给一半，避免抄 ----
    rq = raw.get("real_quotes") or []
    if rq:
        lines = [f"· {str(q.get('line', '')).strip()}" for q in rq[:8] if q.get("line")]
        parts.append(
            "【他说过的话】他本人在视频里说过的句子（观众边听边打的，可能有错字）。"
            "不要当台词用，也不要整句超过 12 个字地搬。只看三件事：一句多长、在哪儿断、落点砸在谁身上。\n"
            + "\n".join(lines)
        )

    # ---- 锚点样本：本轮检索出来的，不是固定三条 ----
    #
    # `shots_enabled=False` 是「重试通道」：实测里出现过三条候选全部逐字照抄
    # 样本的情况（尤其「辞职」「大家庭」这类样本覆盖得特别好的话题）。
    # 那时候问题不在候选，在样本本身——最好的样本会把模型变成复读机。
    # 所以调用方发现「全在抄」时会再要一次「不给样本」的 prompt，
    # 这就是为什么这一层要可关，而不是当成常量。
    if humor_scale > 0 and shots_enabled:
        shots = _pick_shots(
            persona,
            user_text=user_text,
            last_self_story=last_self_story,
            recent_bits=recent_bits,
            last_reply_chars=last_reply_chars,
        )
        digest = retrieval.digest(shots)
        if digest:
            parts.append(digest)

    # ---- guard 姿态下才出现的克制动 ----
    if level in ("L1", "L2") and humor_scale < 1.0:
        parts.append(
            f"【本轮的克制】用户现在不需要被逗笑，损度已降到 {effective_snark}。"
            "保留「我也这样」那一拍，关掉俏皮。不分析他、不教育他、不给三步走方案。"
            + ("\n遇到自我否定时：不要反驳结论（说「你很好」等于没听见），要换掉那把尺子。"
               if level == "L2" else "")
        )

    if last_self_story:
        parts.append(
            "【这条不讲你自己】上一句你刚讲过自己那段。这条只处理他说的这句：接住，落到他身上。"
        )

    if recent_bits:
        parts.append("【这些起手式最近用过，换一个】" + "、".join(recent_bits[-8:]))

    hint = hint_for(candidate_index)
    if hint:
        parts.append("【这一条的形状】" + hint)

    if extra_directive:
        parts.append(f"【本轮附加要求】\n{extra_directive}")

    return "\n\n".join(p.strip() for p in parts if p.strip())


def build_rewrite_prompt(
    persona: Persona,
    draft: str,
    reason: str,
    no_self: bool = False,
    texture: Texture | None = None,
) -> str:
    """不合格时的重写指令。

    v4 只在「格式类」问题（emoji / Markdown / 列表 / 空回复）上还走这条路，
    风格问题交给候选重排。所以这里的指令也从「压风格」改成了「保人味」。
    """
    keep = (
        "这一条不许再出现你自己的一段经历或往事——上一句已经讲过了。"
        if no_self
        else "保留那个最具体的细节（水杯、空调声、日期这类），删掉所有解释和总结。"
    )
    shape = ""
    if texture is not None:
        band = texture.band("reply_chars")
        sent = texture.band("sentences")
        if band and sent:
            shape = (
                f"长度按他的真实分布写：中位 {band.p50:.0f} 字，"
                f"常见区间 {band.p10:.0f}–{band.p90:.0f} 字；中位 {sent.p50:.0f} 句。\n"
            )
    return (
        f"你刚才那条消息不合格，原因：{reason}。\n"
        f"原文：{draft}\n\n"
        f"重写一条人话。{shape}{keep}\n"
        "不要道歉，不要说「好的」，不要复述对方的话。直接给新的一条。"
    )


def variant_directive(kind: str) -> str:
    """「换个说法」的两档：更损 / 更暖。"""
    if kind == "spicier":
        return "把损度提上去：更刻薄地损处境和系统，句子更短更硬。但仍不损用户，也不要变成骂街。"
    if kind == "warmer":
        return "把暖度提上去：去掉嘲讽，多讲一句自己，或者给一个具体的小动作。不许变成空口安慰。"
    return ""


def continuation_directive(kind: str) -> str:
    """用户追问「展开讲讲」时放宽长度。

    这里写「必须」而不是「可以」：真模型面对通篇 3 句的硬约束时，
    会把「可以到 6 句」当成不必要，实测只回 2 句，等于没展开。
    """
    if kind in ("expand", "more"):
        return (
            "用户主动点了「展开讲讲」，本轮必须写到 4–6 句、150–220 字，低于 4 句视为没完成任务。\n"
            "但展开不是写成小作文：仍然保持跳接和硬着陆，中间要有一个自己的具体例子，最后一句仍然短。\n"
            "不许分点列清单、不许加小标题、不许出现「首先/其次/总之」。"
        )
    return ""
