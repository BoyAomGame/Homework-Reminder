"""LLM provider interfaces and the schemas their output must satisfy.

Concrete implementations live in ``app/providers`` and are chosen via
config, so models can be swapped without touching the rest of the app.
Model output is never trusted: it must parse as JSON and validate
against the pydantic schemas here before anything uses it.
"""

import json
import re
from abc import ABC, abstractmethod
from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator


class LLMError(Exception):
    """The provider failed or returned output we could not validate."""


class ParsedAssignment(BaseModel):
    """What the text LLM extracts from one free-text assignment message."""

    subject_name: str | None = None
    teacher_name: str | None = None
    location: str | None = None
    due_date: date | None = None
    due_time: time | None = None
    # "next_class" when the user said e.g. "ส่งคาบหน้า" / "due next class".
    date_hint: Literal["next_class"] | None = None
    notes: str | None = None

    @field_validator("subject_name", "teacher_name", "location", "notes", mode="after")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
        return value or None


class DetectedIntent(BaseModel):
    """Which command a DM intends, plus the arguments that command needs.

    Produced by the intent router (see app/llm/prompts.py). The model
    resolves free-text references ("the chemistry one") to a concrete
    ``assignment_id`` using the pending list it is given, and fills the
    nested ``assignment`` only when the user is adding new homework.
    """

    command: Literal["add", "list", "done", "delete", "edit", "help", "unknown"]
    # Target for done/delete/edit; null when the message names no assignment.
    assignment_id: int | None = None
    # For edit: which field to change and its new value (raw string; dates
    # and times are validated downstream by the same forms parsers the web
    # uses).
    edit_field: Literal[
        "subject", "teacher", "location", "date", "time", "notes"
    ] | None = None
    edit_value: str | None = None
    # Populated only when command == "add".
    assignment: ParsedAssignment | None = None


class ParsedTimetableRow(BaseModel):
    """One timetable slot extracted from an uploaded timetable image."""

    day_of_week: int  # 0 = Monday ... 6 = Sunday
    period_number: int
    subject_name: str
    teacher_name: str | None = None
    room: str | None = None
    start_time: time | None = None
    end_time: time | None = None

    @field_validator("day_of_week")
    @classmethod
    def _day_in_range(cls, value: int) -> int:
        if not 0 <= value <= 6:
            raise ValueError("day_of_week must be 0-6")
        return value

    @field_validator("period_number")
    @classmethod
    def _period_in_range(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("period_number must be 1-20")
        return value


class TextLLM(ABC):
    """Parses free-text assignment messages into structured data."""

    @abstractmethod
    async def parse_assignment(self, text: str, *, prompt_context: str) -> ParsedAssignment:
        """Extract assignment fields from ``text``.

        ``prompt_context`` carries the current date/weekday and the
        user's known subjects (see app/llm/prompts.py).
        Raises LLMError when the model's output cannot be validated.
        """

    @abstractmethod
    async def detect_intent(
        self, text: str, *, prompt_context: str, pending: str
    ) -> DetectedIntent:
        """Decide which command ``text`` intends and extract its arguments.

        ``prompt_context`` carries the current date/weekday and the user's
        known subjects; ``pending`` is a rendered list of the user's open
        assignments so the model can resolve references to a concrete id.
        Raises LLMError when the model's output cannot be validated.
        """


class VisionProvider(ABC):
    """Parses a photo of a class timetable into timetable rows."""

    @abstractmethod
    async def parse_timetable_image(
        self, image_bytes: bytes, content_type: str
    ) -> list[ParsedTimetableRow]:
        """Extract timetable rows from an image.

        Raises LLMError when the model's output cannot be validated.
        """


def extract_json_object(raw: str) -> dict:
    """Pull the first JSON object out of a model response.

    Tolerates markdown code fences and prose around the JSON, since
    models occasionally add them despite instructions not to.
    """
    text = re.sub(r"```(?:json)?", "", raw).strip("` \n")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"No JSON object in model output: {raw[:200]!r}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"Malformed JSON from model: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("Model output is valid JSON but not an object")
    return data


def validate_parsed_assignment(data: dict) -> ParsedAssignment:
    try:
        return ParsedAssignment.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"Assignment JSON failed validation: {exc}") from exc


def validate_detected_intent(data: dict) -> DetectedIntent:
    try:
        return DetectedIntent.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"Intent JSON failed validation: {exc}") from exc


def validate_timetable_rows(data: dict) -> list[ParsedTimetableRow]:
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise LLMError('Timetable JSON must contain a "rows" list')
    validated = []
    for row in rows:
        try:
            validated.append(ParsedTimetableRow.model_validate(row))
        except ValidationError as exc:
            raise LLMError(f"Timetable row failed validation: {exc}") from exc
    return validated
