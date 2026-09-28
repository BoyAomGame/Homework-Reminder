"""VisionProvider for any OpenAI-compatible vision model.

The timetable photo travels as a base64 data URL inside the chat
message, which is the de-facto standard for OpenAI-compatible vision
endpoints. Point VISION_BASE_URL / VISION_MODEL at whichever vision
model is available (a DeepSeek vision model, GPT-4o, Qwen-VL, ...).
"""

import base64

from app.config import Settings
from app.llm.base import (
    LLMError,
    ParsedTimetableRow,
    VisionProvider,
    extract_json_object,
    validate_timetable_rows,
)
from app.llm.prompts import TIMETABLE_SYSTEM_PROMPT
from app.providers.openai_compat import chat_completion


class OpenAICompatVision(VisionProvider):
    def __init__(self, settings: Settings):
        self._api_key = settings.effective_vision_api_key
        self._base_url = settings.effective_vision_base_url
        self._model = settings.vision_model

    async def parse_timetable_image(
        self, image_bytes: bytes, content_type: str
    ) -> list[ParsedTimetableRow]:
        if not self._model:
            raise LLMError(
                "กรุณาตั้งค่าโมเดลอ่านภาพในหน้าตั้งค่าก่อน"
            )
        data_url = (
            f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"
        )
        raw = await chat_completion(
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
            messages=[
                {"role": "system", "content": TIMETABLE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Transcribe this timetable."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
        return validate_timetable_rows(extract_json_object(raw))
