import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str
    be_base_url: str
    debug: bool = False


def load_settings() -> Settings:
    load_dotenv(
        Path(__file__).resolve().parent / ".env",
        override=False,
    )

    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    model = os.getenv("LLM_MODEL", "").strip()
    provider_api_key = {
        "deepseek": os.getenv("DEEPSEEK_API_KEY", ""),
        "qwen": os.getenv("DASHSCOPE_API_KEY", ""),
    }.get(provider, "").strip()
    # Keep LLM_API_KEY as a backward-compatible fallback for existing .env files.
    api_key = provider_api_key or os.getenv("LLM_API_KEY", "").strip()
    default_base_url = {
        "deepseek": "https://api.deepseek.com",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }.get(provider)
    base_url = os.getenv("LLM_BASE_URL", default_base_url or "").strip().rstrip("/")
    be_base_url = os.getenv(
        "BE_BASE_URL", "http://127.0.0.1:8000"
    ).strip().rstrip("/")

    if provider not in {"deepseek", "qwen"}:
        raise ValueError("LLM_PROVIDER 仅支持 deepseek 或 qwen")

    if not model:
        raise ValueError("请配置 LLM_MODEL")

    if not api_key:
        raise ValueError("请配置 LLM_API_KEY")

    if not base_url or not be_base_url:
        raise ValueError("模型和后端服务地址不能为空")

    debug = os.getenv("AGENT_DEBUG", "false").strip().lower()
    if debug not in {"true", "false", "1", "0"}:
        raise ValueError("AGENT_DEBUG 必须为 true/false 或 1/0")

    return Settings(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        be_base_url=be_base_url,
        debug=debug in {"true", "1"},
    )
