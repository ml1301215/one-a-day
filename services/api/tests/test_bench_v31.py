"""v3.1 的守门测试：这一版只有四件事，每件都要有能被测出来的痕迹。

v3.1 的改动全部来自同一份新材料——三条视频的官方字幕（真实逐字稿）
和带时间轴的弹幕对齐之后量出来的数字。既然依据是数字，验收也必须是数字：
「所以」的剂量上限、否定排位在不在场、具体物这条规则有没有被写进去、
收场动作有没有被编译进 prompt、对练台有没有真的挂上。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import quality
from app.config import settings
from app.main import app
from app.persona.loader import get_persona
from app.prompt import builder


@pytest.fixture(scope="module")
def persona():
    return get_persona(str(settings.persona_path))


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- 人格包


def test_persona_is_v4(persona):
    assert persona.version == "4.0.0"


def test_engine_has_dosage_rule(persona):
    """剂量是这一版唯一新增的硬数字。

    没有它，「推因果」这一拍会被模型当成每轮必做的动作，
    而真实语料里「所以」每分钟只有 0.4–2.4 次——差一个数量级。
    """
    dosage = persona.raw["speech_engine"].get("dosage", "")
    assert dosage, "speech_engine 缺少 dosage"
    assert "三轮" in dosage and "所以" in dosage


def test_negated_ranking_device_exists(persona):
    ids = [d.id for d in persona.devices]
    assert "negated_ranking" in ids
    d = next(x for x in persona.devices if x.id == "negated_ranking")
    # 权重必须高到能进 prompt（top5），否则写了等于没写
    assert d.weight >= 1.0
    assert len(persona.devices) >= 1
    assert d.examples, "没有例子的装置等于一条形容词"


def test_concrete_anchor_is_a_rule(persona):
    ids = [r.get("id") for r in persona.raw["speech_rhythm"]["rules"]]
    assert "concrete_anchor" in ids


def test_landing_beat_has_exit_step(persona):
    """落完之后那个「走开」的半步。三条稿子里有两条是这么收的。"""
    assert persona.raw["chat_shape"]["落"].get("exit"), "落 缺少 exit"


def test_negation_is_stance_not_anecdote():
    """「我不是…」是表态，不是一段经历。

    不区分的话，「否定排位」每一句都会把自述标记点亮，
    对练台上就会显示「讲自己：是」——那会让测试的人以为检测坏了，
    进而去改一个本来正确的判据。
    """
    assert not quality.self_story("我不是能扛。是我回去也是一个人。")
    assert not quality.self_story("我不是心态好。是我想不出还能坏到哪去。")
    # 但真的在讲经历的，不许漏
    assert quality.self_story("我去年也不是没干过。")
    assert quality.self_story("我妈也干过。")


# ---------------------------------------------------------------- prompt


def test_prompt_injects_dosage_and_exit(persona):
    system = builder.build_system_prompt(persona, level="L0")
    assert "剂量：" in system
    assert "收场动作：" in system
    assert "否定排位" in system


def test_device_block_is_capped_at_five(persona):
    """条数放宽到 5，但不能无限长——prompt 长度是真实成本。"""
    system = builder.build_system_prompt(persona, level="L0")
    block = system.split("【你的手艺】")[1].split("【你怎么说话】")[0]
    assert block.count("◆") == 5


# ---------------------------------------------------------------- 接口


def test_chat_returns_quality_report(client):
    d = client.post("/api/chat", json={"message": "今天又被领导说了", "device_id": "v31"}).json()
    q = d["quality"]
    for k in ("ok", "issues", "sentences", "chars", "self_story", "copied", "rewrite", "gate"):
        assert k in q
    assert isinstance(q["self_story"], bool)
    assert d["persona"]["version"] == "4.0.0"
    # v4：候选重排的体检单要能被看到，否则「为什么挑了这一条」又只能去翻 sqlite
    assert "candidates" in q
    assert "texture" in q


def test_gate_can_be_overridden_per_request(client):
    """对练台要在同一个会话里 A/B 两种闸门，否则测出来的差异里混着「重启过服务」。"""
    d = client.post(
        "/api/chat",
        json={"message": "今天又被领导说了", "device_id": "v31b", "gate": "strict"},
    ).json()
    assert d["quality"]["gate"] == "strict"


def test_no_self_gate_only_bites_on_second_story():
    """连着两条讲自己才算不合格；单条讲自己是他最核心的一拍，不能碰。"""
    from app.routes.chat import _apply_no_self

    r1 = quality.QualityReport(ok=True)
    _apply_no_self(r1, "我上回也这样。", True)
    assert r1.ok is False
    assert "又讲了一遍" in r1.issues[-1]

    r2 = quality.QualityReport(ok=True)
    _apply_no_self(r2, "我上回也这样。", False)
    assert r2.ok is True

    r3 = quality.QualityReport(ok=True)
    _apply_no_self(r3, "明天还得去。", True)
    assert r3.ok is True


def test_bench_page_is_served(client):
    r = client.get("/demo")
    assert r.status_code == 200
    assert "对练台" in r.text
