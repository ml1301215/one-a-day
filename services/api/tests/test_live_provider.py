"""真模型冒烟测试（默认跳过）。

跑法：

    OAD_RUN_LIVE_TESTS=1 pytest tests/test_live_provider.py -q

前提是已配好 OAD_DEEPSEEK_API_KEY（.env 或环境变量）。不设开关时整体跳过，
所以 CI 与 `pytest` 全量跑都不会联网、不花钱。
"""

import os

import pytest

from app.config import settings
from app.llm.providers import get_provider

pytestmark = pytest.mark.skipif(
    os.environ.get("OAD_RUN_LIVE_TESTS") != "1",
    reason="真模型测试需显式开启：OAD_RUN_LIVE_TESTS=1",
)


def test_deepseek_answers():
    if not settings.deepseek_api_key:
        pytest.skip("OAD_DEEPSEEK_API_KEY 未配置")

    import asyncio

    p = get_provider("deepseek")
    text = asyncio.run(
        p.complete(
            system="你只回答两个字，不要解释。",
            messages=[{"role": "user", "content": "回答：收到"}],
            temperature=0.2,
            top_p=0.9,
            presence_penalty=0.0,
            max_tokens=20,
        )
    )
    assert text.strip()
    assert len(text) < 200
