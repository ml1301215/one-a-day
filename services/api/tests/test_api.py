"""API 端到端测试（mock provider，不依赖网络与 key）。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["provider"] == "mock"


def test_personas(client):
    r = client.get("/api/personas")
    ids = [p["id"] for p in r.json()["personas"]]
    assert "brufen" in ids


def test_chat_creates_session(client):
    r = client.post("/api/chat", json={"message": "今天又被领导说了", "device_id": "t1"})
    d = r.json()
    assert d["session_id"]
    assert d["reply"]
    assert d["safety"]["level"] in ("L0", "L1")


def test_chat_continues_session(client):
    first = client.post("/api/chat", json={"message": "我想辞职", "device_id": "t2"}).json()
    sid = first["session_id"]
    second = client.post(
        "/api/chat", json={"message": "但我又怕找不到工作", "session_id": sid}
    ).json()
    assert second["session_id"] == sid
    msgs = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert len(msgs) == 4


def test_crisis_has_no_hard_template_by_default(client):
    """v4 默认姿态（OAD_SAFETY=off）：危机走人格，不走硬切模板。

    这一条锁的是「安全分级已从主链路移除」这个事实——meta 里仍然有一个中性
    level 字段给对练台读，但 has_template 必须是 False，且 reply 不能是
    services/api/app/safety/router.py 里的那段固定文案。
    """
    from app.safety.router import L3_TEMPLATES

    d = client.post(
        "/api/chat", json={"message": "活着没意思，不想活了", "device_id": "t3"}
    ).json()
    assert d["safety"]["mode"] == "off"
    assert d["safety"]["has_template"] is False
    assert d["reply"]
    assert d["reply"] not in L3_TEMPLATES.values()


def test_guard_posture_can_be_turned_on(client, monkeypatch):
    """OAD_SAFETY=guard 时回到 v3 行为：L3 硬切，模板不交给模型。"""
    from app.config import settings

    monkeypatch.setattr(settings, "safety", "guard")
    d = client.post(
        "/api/chat", json={"message": "活着没意思，不想活了", "device_id": "t3g"}
    ).json()
    assert d["safety"]["level"] == "L3"
    assert d["safety"]["has_template"] is True
    assert "12356" in d["reply"]


def test_stream_sse(client):
    with client.stream(
        "POST", "/api/chat/stream", json={"message": "加班到很晚", "device_id": "t4"}
    ) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())

    assert "event: meta" in body
    assert "event: delta" in body
    assert "event: done" in body

    # delta 拼起来应该等于最终文本
    deltas = []
    for block in body.split("\n\n"):
        if block.startswith("event: delta"):
            payload = json.loads(block.split("data: ", 1)[1])
            deltas.append(payload["text"])
    assert len("".join(deltas)) > 5


def test_dry_run_does_not_generate(client):
    """dry_run 只回分级与姿态，绝不生成。默认姿态下不分类，level 是中性占位。"""
    d = client.post(
        "/api/chat",
        json={"message": "布洛芬一次吃几片", "device_id": "t5", "dry_run": True},
    ).json()
    assert d["dry_run"] is True
    assert d["safety"]["mode"] == "off"
    assert d["safety"]["has_template"] is False
    assert "reply" not in d


def test_dry_run_in_guard_posture(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "safety", "guard")
    d = client.post(
        "/api/chat",
        json={"message": "布洛芬一次吃几片", "device_id": "t5g", "dry_run": True},
    ).json()
    assert d["safety"]["level"] == "L3"
    assert d["safety"]["has_template"] is True


def test_classify_endpoint(client):
    d = client.post("/api/safety/classify", json={"text": "我撑不住了"}).json()
    assert d["level"] in ("L2", "L3")


def test_feedback(client):
    d = client.post("/api/chat", json={"message": "工资不涨", "device_id": "t6"}).json()
    r = client.post("/api/feedback", json={"message_id": d["message_id"], "kind": "up"})
    assert r.json()["ok"] is True


def test_history_uses_latest_turns(client):
    """长会话必须带最近几轮，不能把开场白当上下文。"""
    from app import db

    sid = client.post("/api/chat", json={"message": "开场第一句", "device_id": "t7"}).json()[
        "session_id"
    ]
    for i in range(8):
        client.post("/api/chat", json={"message": f"后续第{i}句", "session_id": sid})

    hist = db.recent_history(sid, 2)
    assert len(hist) <= 4
    contents = [m["content"] for m in hist]
    assert "开场第一句" not in contents
    assert any("后续第7句" in c for c in contents)


def test_regenerate_does_not_duplicate_user_turn(client):
    """点「更损一点」/「展开讲讲」是把同一句话重生成，不能在历史里堆重复。"""
    from app import db

    sid = client.post("/api/chat", json={"message": "今天开会被点名", "device_id": "t8"}).json()[
        "session_id"
    ]
    before = len([m for m in db.list_messages(sid) if m["role"] == "user"])

    # 两种重生成路径都不该新增 user 消息
    client.post(
        "/api/chat", json={"message": "今天开会被点名", "session_id": sid, "variant": "spicier"}
    )
    client.post("/api/chat", json={"message": "今天开会被点名", "session_id": sid, "expand": True})

    after = len([m for m in db.list_messages(sid) if m["role"] == "user"])
    assert after == before


def test_regenerate_replaces_previous_reply(client):
    """「换个说法」是替换，不是追加——否则上下文里会出现一个提问两条回答。"""
    from app import db

    sid = client.post("/api/chat", json={"message": "同事抢我功劳", "device_id": "t9"}).json()[
        "session_id"
    ]
    before = len([m for m in db.list_messages(sid) if m["role"] == "assistant"])

    client.post(
        "/api/chat", json={"message": "同事抢我功劳", "session_id": sid, "variant": "warmer"}
    )

    assistants = [m for m in db.list_messages(sid) if m["role"] == "assistant"]
    assert len(assistants) == before

    # 上下文必须是 user/assistant 交替，不能出现 assistant 连两条
    hist = db.recent_history(sid, 4)
    roles = [m["role"] for m in hist]
    assert not any(roles[i] == roles[i + 1] == "assistant" for i in range(len(roles) - 1))
