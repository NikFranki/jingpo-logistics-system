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


def load_settings() -> Settings:
    load_dotenv(
        Path(__file__).resolve().parent / ".env",
        override=False,
    )

    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    model = os.getenv("LLM_MODEL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base_url = os.getenv(
        "LLM_BASE_URL", "https://api.deepseek.com"
    ).strip().rstrip("/")
    be_base_url = os.getenv(
        "BE_BASE_URL", "http://127.0.0.1:8000"
    ).strip().rstrip("/")

    if provider != "deepseek":
        raise ValueError("当前仅支持 LLM_PROVIDER=deepseek")

    if not model:
        raise ValueError("请配置 LLM_MODEL")

    if not api_key:
        raise ValueError("请配置 LLM_API_KEY")

    if not base_url or not be_base_url:
        raise ValueError("模型和后端服务地址不能为空")

    return Settings(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        be_base_url=be_base_url,
    )