"""质量闸门测试：机器能判的错误，就不该流到用户面前。"""

from app import quality
from app.config import settings
from app.persona.loader import get_persona

PERSONA = get_persona(str(settings.persona_path))


def test_accepts_good_reply():
    text = "他说这话的时候，大概率是他自己昨晚没睡好。明天让他挑一个方向。"
    r = quality.check(text, PERSONA)
    assert r.ok, r.issues


def test_rejects_too_long():
    text = "。".join(["这是一句话"] * 6) + "。"
    r = quality.check(text, PERSONA)
    assert not r.ok
    assert any("句数" in i for i in r.issues)


def test_rejects_emoji():
    r = quality.check("今天辛苦了🙂明天继续。", PERSONA)
    assert not r.ok
    assert any("emoji" in i for i in r.issues)


def test_rejects_markdown_and_lists():
    r = quality.check("**结论**：先睡。\n- 第一\n- 第二", PERSONA)
    assert not r.ok


def test_rejects_anti_bits():
    r = quality.check("加油，你可以的。", PERSONA)
    assert not r.ok
    assert any("禁用表达" in i for i in r.issues)


def test_rejects_ai_tone():
    r = quality.check("我理解你的感受，这确实很难。", PERSONA)
    assert not r.ok


def test_truncate_keeps_head_and_tail():
    """超长时保留首尾：开头负责被接住，结尾负责能做点什么。"""
    text = "开头一句。中间解释一。中间解释二。中间解释三。结尾一句。"
    out = quality.truncate_to_shape(text, PERSONA)
    assert out.startswith("开头一句")
    assert "结尾一句" in out
    assert quality.count_sentences(out) <= PERSONA.sentence_max


def test_extract_bits():
    bits = quality.extract_bits("这是一句比较长的话。另一句也不短。")
    assert len(bits) == 2
    assert all(len(b) == 6 for b in bits)
