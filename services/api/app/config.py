"""运行配置。全部走环境变量，不引入 pydantic-settings 以免多一层依赖。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = REPO_ROOT / "packages" / "persona"

# 可选加载 .env：装了 python-dotenv 就读，没装也不报错（容器里通常走 env 注入）。
# 顺序上后加载的不覆盖已存在的环境变量，方便 `OAD_PROVIDER=mock uvicorn ...` 临时覆盖。
try:
    from dotenv import load_dotenv

    for _candidate in (REPO_ROOT / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if _candidate.is_file():
            load_dotenv(_candidate, override=False)
except ImportError:  # pragma: no cover - 没装 dotenv 时保持纯环境变量
    pass


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key) or default)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        return default


@dataclass
class Settings:
    # 默认 mock：保证 clone 之后不配任何 key 也能聊。
    provider: str = field(default_factory=lambda: _env("OAD_PROVIDER", "mock").lower())
    persona_id: str = field(default_factory=lambda: _env("OAD_PERSONA", "brufen"))

    # OpenAI 兼容（Qwen / Moonshot / OpenAI 都能用）
    openai_base_url: str = field(
        default_factory=lambda: _env("OAD_OPENAI_BASE_URL", "https://api.openai.com/v1")
    )
    openai_api_key: str = field(default_factory=lambda: _env("OAD_OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OAD_OPENAI_MODEL", "gpt-4o-mini"))

    # DeepSeek：同样是 OpenAI 兼容协议，只是端点与模型名固定，单独一组变量
    deepseek_base_url: str = field(
        default_factory=lambda: _env("OAD_DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    deepseek_api_key: str = field(default_factory=lambda: _env("OAD_DEEPSEEK_API_KEY"))
    deepseek_model: str = field(
        default_factory=lambda: _env("OAD_DEEPSEEK_MODEL", "deepseek-chat")
    )

    # Ollama
    ollama_base_url: str = field(
        default_factory=lambda: _env("OAD_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    )
    ollama_model: str = field(default_factory=lambda: _env("OAD_OLLAMA_MODEL", "qwen2.5:7b"))

    # 采样：方案里定的 0.9 / 0.95 / presence 0.6
    temperature: float = field(default_factory=lambda: _env_float("OAD_TEMPERATURE", 0.9))
    top_p: float = field(default_factory=lambda: _env_float("OAD_TOP_P", 0.95))
    presence_penalty: float = field(
        default_factory=lambda: _env_float("OAD_PRESENCE_PENALTY", 0.6)
    )
    request_timeout: float = field(default_factory=lambda: _env_float("OAD_TIMEOUT", 60.0))

    # 存储
    db_path: str = field(
        default_factory=lambda: _env(
            "OAD_DB_PATH", str(REPO_ROOT / "services" / "api" / "data" / "oneadday.db")
        )
    )

    # 服务
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in _env("OAD_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
            if o.strip()
        ]
    )
    rate_limit_per_min: int = field(default_factory=lambda: _env_int("OAD_RATE_LIMIT", 30))
    history_turns: int = field(default_factory=lambda: _env_int("OAD_HISTORY_TURNS", 8))

    # 多候选挑选（方案 08：同轮生成 3 候选，按人味分选 1）
    candidates: int = field(default_factory=lambda: _env_int("OAD_CANDIDATES", 1))

    @property
    def persona_path(self) -> Path:
        return PERSONA_DIR / self.persona_id / "persona.yaml"


settings = Settings()
