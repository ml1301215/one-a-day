"""人格包 v3（引擎版）的行为测试。

这些测试守的不是「代码能跑」，而是本次改版真正要解决的两个问题：
1. 引擎、语流、真实原话有没有真的进到 prompt 里（v1 写了很多规则但 few_shots 从未注入）；
2. 「像人」这件事有没有可判定的下限（断句不算超长、AI 腔能被拦下）。
"""

from __future__ import annotations

import re

import pytest

from app import quality
from app.config import settings
from app.persona.loader import get_persona
from app.prompt import builder


@pytest.fixture(scope="module")
def persona():
    return get_persona(str(settings.persona_path))


def test_persona_is_v3_with_engine(persona):
    assert persona.version.startswith("3.")
    raw = persona.raw
    # v3 的核心结构必须存在：
    # speech_engine 是「谐音伪因果」的发动机，缺了它等于退回 v2 的「只有语流」；
    # real_quotes 是唯一一批他本人的句子，缺了它模型只能模仿我们的仿写。
    for key in ("speech_engine", "real_quotes", "speech_rhythm", "verbal_tics",
                "chat_shape", "anti_ai_flavor", "imagery_pool"):
        assert key in raw, f"人格包缺少 {key}"
    assert raw["speech_rhythm"].get("rules"), "语流规则不能为空"


def test_system_prompt_injects_few_shots(persona):
    """v1 的硬伤：示例写在 yaml 里却从未进 prompt，模型没有可模仿的样本。"""
    system = builder.build_system_prompt(persona, level="L0")
    shots = persona.raw["few_shots"]
    assert "语感参照" in system
    # 至少有一条样本的正文出现在 prompt 里
    assert any(str(s["reply"]).split("\n")[0].strip() in system for s in shots)
    # 且必须附带「不许照抄情节」的约束
    assert "不许照搬" in system or "照抄" in system


def test_system_prompt_carries_rhythm_rules(persona):
    system = builder.build_system_prompt(persona, level="L0")
    assert "语流" in system
    assert "不许有过渡词" in system
    assert "进入方式要换着来" in system
    # 反 AI 味清单要进 prompt，而不是只写在 yaml 里
    assert "反 AI 味" in system


def test_l2_gets_no_few_shots(persona):
    """L2 给样本会被逐字照抄，用户每问一次得到同一段话。"""
    system = builder.build_system_prompt(persona, level="L2", humor_scale=0.0)
    assert "语感参照" not in system
    assert "换掉那把尺子" in system  # 改由规则承担


def test_short_fragment_not_counted_as_sentence():
    """「当众说。我去年也挨过一次。」是断句，不是两句长句。"""
    assert quality.count_sentences("当众说。") == 0
    assert quality.count_sentences("当众说。我去年也挨过一次。") == 1
    assert quality.count_sentences("我去年也挨过一次，脑子嗡一下。") == 1


def test_broken_sentence_passes_gate(persona):
    """这个人把一句话硬切成两截，不该因此被判超长。"""
    text = "当众说。\n我去年也挨过一次，脑子嗡一下，后面他讲什么全没听见。\n回去把最硬的那条发他。就一条。"
    rep = quality.check(text, persona, humor_scale=1.0)
    assert rep.ok, rep.reason()


@pytest.mark.parametrize(
    "text",
    [
        "其实，这件事也不是你的问题。",
        "行吧。说白了就是没人管。",
        "总之，明天再说。",
    ],
)
def test_leading_filler_flagged(persona, text):
    rep = quality.check(text, persona, humor_scale=1.0)
    assert not rep.ok
    assert "填充词" in rep.reason()


def test_inline_filler_not_flagged(persona):
    """句中出现的「其实」是正常语义，不能一刀切禁掉。"""
    rep = quality.check("我妈也说过类似的话，后来发现她其实怕的是亲戚问起来。", persona)
    assert rep.ok, rep.reason()


def test_parallel_structure_flagged(persona):
    text = "这不是你的问题，而是尺子的问题。不是他不懂，而是他不想懂。"
    rep = quality.check(text, persona)
    assert not rep.ok
    assert "对称排比" in rep.reason()


def test_no_short_sentence_flagged(persona):
    """通篇没有短句 = 在写段落，不是发消息。"""
    text = (
        "你今天遇到这件事我一点都不意外因为这家公司一直都是这样运作的。"
        "他们把所有人的付出都当成理所当然并且从不觉得需要道谢。"
        "所以你现在这种感受其实是非常正常而且完全可以理解的反应。"
    )
    rep = quality.check(text, persona)
    assert not rep.ok
    assert "短句" in rep.reason() or "填充词" in rep.reason()


def test_l3_templates_survive_new_gate(persona):
    """安全模板不能被「反 AI 味」规则判不合格，否则危机时刻会被反复重写。"""
    from app.safety.router import classify

    for text in ("活着没意思", "我妈突然说胸口疼喘不上气", "公司拖欠工资我想去法院告"):
        v = classify(text)
        assert v.template
        rep = quality.check(v.template, persona, humor_scale=0.0)
        assert rep.ok, f"{v.template_key}: {rep.reason()}"


def test_verbal_tics_are_documented_not_spammed(persona):
    """口头禅必须有证据字段，且 prompt 里明确「最多一次」。"""
    tics = persona.raw["verbal_tics"]
    assert any(t["word"] == "很简单" for t in tics)
    for t in tics:
        assert t.get("evidence"), f"{t['word']} 缺证据"
    system = builder.build_system_prompt(persona, level="L0")
    assert "最多两次" in system


def test_truncate_keeps_line_breaks(persona):
    """超长降级时用换行拼接。用句号拼成一段会抹掉断句感，气泡里糊成一坨。"""
    text = (
        "第一句写得很长，超过五个字了。我上回也这样。"
        "第二句也在讲一个细节，还是长的。第三句继续讲，也是长的。"
        "第四句还是长的，讲不完。最后一句是落点。"
    )
    out = quality.truncate_to_shape(text, persona)
    assert "\n" in out, "被压成了单行，断句感丢失"
    assert "落点" in out, "末尾的落点句被砍掉了"
    assert len(out.split("\n")) <= persona.sentence_max + 1


def test_truncate_merges_short_fragment(persona):
    """断句碎片要并进上一句，不能单独占一行变成孤零零两个字。"""
    text = "第一句很长很长很长，超过五个字。好。第二句也很长，讲细节。"
    out = quality.truncate_to_shape(text, persona)
    assert "好" in out
    assert not any(len(line.strip("。")) < 3 for line in out.split("\n") if line.strip())


def test_copied_shot_flagged(persona):
    """模型会直接抄示例句。抄了就判不合格，让它重写。

    v2 这里写死了一句具体台词，结果示例一换，这条用例就变成永远失败的空跑
    （或者更糟：永远通过但测的是已经不存在的句子）。
    改成从当前人格包里动态取最长的一句来抄，示例怎么改都能测到真东西。
    """
    lines = [
        ln.strip()
        for shot in persona.raw["few_shots"]
        for ln in str(shot.get("reply", "")).split("\n")
        if ln.strip()
    ]
    target = max(lines, key=lambda x: len(re.sub(r"[\s。，、！？…「」（）—]", "", x)))
    assert len(re.sub(r"[\s。，、！？…「」（）—]", "", target)) >= 12, "示例里没有够长的句子，用例失去意义"

    rep = quality.check(target, persona, humor_scale=1.0)
    assert not rep.ok
    assert "照抄" in rep.reason()


def test_original_wording_not_flagged(persona):
    rep = quality.check("署名被人动了。我工位旁边那人的招财猫手掉了一只，还在摇。", persona)
    assert rep.ok, rep.reason()


def test_truncate_drops_dangling_fragment(persona):
    """模型被截断时留下的半句（无句号）不能拼进回复里，否则出现「就成了他的。加一列」。"""
    text = (
        "第一句很长很长很长，超过五个字。第二句也长，讲一个细节在这里。"
        "第三句继续写，还是长的内容。第四句还在写，长得很。加一列"
    )
    out = quality.truncate_to_shape(text, persona)
    assert not out.rstrip().endswith("加一列")
    assert out.endswith("。")


def test_self_story_detection(persona):
    """「讲自己」要能被识别出来，否则连着两轮都在讲自己没人拦得住。"""
    assert quality.self_story("我上回也这样。")
    assert quality.self_story("我去年也翻过那个对话框。")
    assert quality.self_story("我当年也签过。")
    assert not quality.self_story("明天还得去。")
    assert not quality.self_story("他这是署名。属名。属谁的。")


def test_raw_mode_keeps_style_issues_advisory(persona):
    """raw 模式：风格类问题只记录，不触发重写。

    v2 的门是「任何 issue 都重写」，结果是模型被按规则改成一句通顺但不像人的话。
    这条用例锁住 v3 的行为：句数超了照样记录，但不判失败。
    """
    # 注意：count_sentences 不计 <5 字的碎片，所以每句都要写成真正的句子，
    # 否则「第五句。」这种会被当成断句碎片，测不到句数上限。
    text = "这是一句完整的句子。" * 8
    strict = quality.check(text, persona, strict=True)
    loose = quality.check(text, persona, strict=False)
    assert not strict.ok, "严格模式下超句数必须拦"
    assert loose.ok, loose.reason()
    assert any("句数" in i for i in loose.issues), "raw 模式仍要记录，只是不拦"


def test_raw_mode_still_blocks_format(persona):
    """raw 不等于什么都放行：格式与助手腔仍然硬拦。"""
    assert not quality.check("**重点**在这。", persona, strict=False).ok
    assert not quality.check("我理解你的感受，这很正常。", persona, strict=False).ok
    assert not quality.check("今天辛苦了🙂", persona, strict=False).ok
