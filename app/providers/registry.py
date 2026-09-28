"""AI providers configured from the private settings page."""

from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decrypt_secret
from app.db.models import AppSettings
from app.llm.base import TextLLM, VisionProvider

_settings = None


async def reload_configuration(session: AsyncSession) -> None:
    global _settings
    row = await session.get(AppSettings, 1)
    _settings = SimpleNamespace(
        deepseek_api_key=decrypt_secret(row.text_api_key_encrypted) if row else "",
        deepseek_base_url=row.text_base_url if row else "https://api.deepseek.com/v1",
        deepseek_text_model=row.text_model if row else "deepseek-chat",
        effective_vision_api_key=(
            decrypt_secret(row.vision_api_key_encrypted) or decrypt_secret(row.text_api_key_encrypted)
            if row else ""
        ),
        effective_vision_base_url=(row.vision_base_url or row.text_base_url) if row else "https://api.deepseek.com/v1",
        vision_model=row.vision_model if row else "",
    )


def get_text_llm() -> TextLLM:
    from app.providers.deepseek_text import DeepSeekTextLLM

    if _settings is None:
        raise RuntimeError("AI settings have not loaded")
    return DeepSeekTextLLM(_settings)


def get_vision_provider() -> VisionProvider:
    from app.providers.openai_compat_vision import OpenAICompatVision

    if _settings is None:
        raise RuntimeError("AI settings have not loaded")
    return OpenAICompatVision(_settings)
