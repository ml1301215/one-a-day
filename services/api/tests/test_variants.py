"""变体重生成测试：更损 / 更暖 必须真的换说法，不能点了没反应。

默认 provider 是 mock（零依赖、no API key 也能跑）。mock 没有语义理解，
只能靠读取 prompt 里的变体指令来换模板，所以这里直接钉住「三档产出不同」。
"""

import asyncio

import pytest

from app.llm.providers import MockProvider
from app.prompt.builder import variant_directive

MSG = "今天又被领导当众说，我的方案没有灵魂"


def _gen(variant: str, level: str = "L0", msg: str = MSG) -> str:
    system = f"你是一日一片。本轮到 {level} 级。"
    directive = variant_directive(variant)
    if directive:
        system += "\n" + directive
    return asyncio.run(
        MockProvider().complete(
            system=system,
            messages=[{"role": "user", "content": msg}],
            temperature=0.8,
            top_p=0.9,
            presence_penalty=0.3,
        )
    )


@pytest.mark.parametrize("variant", ["default", "spicier", "warmer"])
def test_variant_differs_from_default(variant):
    if variant == "default":
        pytest.skip("默认档本身就是基准")
    assert _gen(variant) != _gen("default")


def test_three_variants_are_mutually_distinct():
    outs = {v: _gen(v) for v in ("default", "spicier", "warmer")}
    assert len(set(outs.values())) == 3


def test_spicier_never_targets_the_user():
    """损档损处境，不许损用户。这两类是唯一的越界口子。"""
    text = _gen("spicier")
    for banned in ("你活该", "你就是不行", "你没用", "怪你自己"):
        assert banned not in text


def test_warmer_drops_sarcasm_but_keeps_action():
    """暖档不许变成空口安慰：必须仍然有可执行动作。

    动作由动词标记判断，不锚定具体日期词——落地句可能是「把这句话记下来」，
    也可能是「明天发他挑一个」，两者都算有动作。
    """
    text = _gen("warmer")
    action_markers = ("把", "记", "发", "找", "问", "拆", "写", "挑", "列")
    assert any(k in text for k in action_markers)
    for banned in ("加油", "会好的", "想开点", "抱抱"):
        assert banned not in text


def test_low_mood_ignores_spicier():
    """低落态不开放增损：点「更损」也不许变得更刻薄。"""
    assert _gen("spicier", "L1", "最近有点撑不住，什么都不想做") == _gen(
        "default", "L1", "最近有点撑不住，什么都不想做"
    )


def test_variant_keeps_sentence_budget():
    for v in ("default", "spicier", "warmer"):
        text = _gen(v)
        assert 1 <= text.count("。") <= 4
