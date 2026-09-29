from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from config import Settings


def create_chat_model(settings: Settings) -> BaseChatModel:
    if settings.provider == "deepseek":
        return ChatDeepSeek(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=45,
            max_retries=0,
        )

    raise ValueError("当前不支持该模型供应商")