"""DeepSeek implementation of the TextLLM interface."""

from app.config import Settings
from app.llm.base import (
    DetectedIntent,
    ParsedAssignment,
    TextLLM,
    extract_json_object,
    validate_detected_intent,
    validate_parsed_assignment,
)
from app.llm.prompts import ASSIGNMENT_SYSTEM_PROMPT, INTENT_SYSTEM_PROMPT
from app.providers.openai_compat import chat_completion


class DeepSeekTextLLM(TextLLM):
    def __init__(self, settings: Settings):
        self._api_key = settings.deepseek_api_key
        self._base_url = settings.deepseek_base_url
        self._model = settings.deepseek_text_model

    async def parse_assignment(
        self, text: str, *, prompt_context: str
    ) -> ParsedAssignment:
        raw = await chat_completion(
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
            messages=[
                {"role": "system", "content": ASSIGNMENT_SYSTEM_PROMPT},
                {"role": "user", "content": f"{prompt_context}\n\nMessage:\n{text}"},
            ],
        )
        return validate_parsed_assignment(extract_json_object(raw))

    async def detect_intent(
        self, text: str, *, prompt_context: str, pending: str
    ) -> DetectedIntent:
        raw = await chat_completion(
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"{prompt_context}\n\n{pending}\n\nMessage:\n{text}",
                },
            ],
        )
        return validate_detected_intent(extract_json_object(raw))
