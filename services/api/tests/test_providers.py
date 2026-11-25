"""provider 解析测试：不联网，只验配置接线与错误提示是否说人话。

真调模型的冒烟测试在 `test_live_provider.py`，需要显式设置
`OAD_RUN_LIVE_TESTS=1` 才跑，避免 CI 与本地反复花钱。
"""

import pytest

from app.config import settings
from app.llm.providers import (
    DeepSeekProvider,
    MockProvider,
    OllamaProvider,
    OpenAIProvider,
    _explain_http_error,
    get_provider,
)


def test_provider_resolution():
    assert isinstance(get_provider("mock"), MockProvider)
    assert isinstance(get_provider("deepseek"), DeepSeekProvider)
    assert isinstance(get_provider("ollama"), OllamaProvider)
    assert isinstance(get_provider("openai"), OpenAIProvider)
    # 未知名字必须落到 mock，不能静默变成要花钱的 provider
    assert isinstance(get_provider("whatever-typo"), MockProvider)


def test_deepseek_reads_its_own_vars(monkeypatch):
    """deepseek 用 OAD_DEEPSEEK_*，不去蹭 OAD_OPENAI_*。"""
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-chat")
    p = get_provider("deepseek")
    assert (p.base_url, p.model, p.api_key) == (
        "https://api.deepseek.com",
        "deepseek-chat",
        "sk-test",
    )


def test_deepseek_is_openai_compatible():
    """协议复用：DeepSeek 只是换了端点与模型名。"""
    assert issubclass(DeepSeekProvider, OpenAIProvider)


def test_missing_key_message_names_the_right_env(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    p = get_provider("deepseek")
    with pytest.raises(RuntimeError) as ei:
        import asyncio

        asyncio.run(
            p.complete(
                system="x",
                messages=[{"role": "user", "content": "y"}],
                temperature=0.9,
                top_p=0.95,
                presence_penalty=0.6,
            )
        )
    assert "OAD_DEEPSEEK_API_KEY" in str(ei.value)


@pytest.mark.parametrize(
    "status,fragment",
    [
        (401, "API key 无效"),
        (402, "余额不足"),
        (429, "限流"),
        (503, "过载"),
        (418, "非 2xx"),
    ],
)
def test_http_error_is_translated(status, fragment):
    msg = _explain_http_error(status, '{"error":"x"}', "deepseek")
    assert fragment in msg
    assert "deepseek" in msg
