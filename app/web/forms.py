"""Small helpers for parsing and cleaning HTML form values."""

from datetime import date, datetime, time


class FormError(ValueError):
    """A user-facing validation problem with a form field."""


def clean_text(value: str | None, *, max_length: int = 500) -> str | None:
    """Strip whitespace; empty strings become None."""
    if value is None:
        return None
    value = value.strip()
    if len(value) > max_length:
        raise FormError(f"ข้อความยาวเกินไป (สูงสุด {max_length} ตัวอักษร)")
    return value or None


def parse_date(value: str, *, field: str = "วันที่") -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise FormError(f"{field}ไม่ถูกต้อง: ใช้รูปแบบ YYYY-MM-DD") from None


def parse_optional_time(value: str | None, *, field: str = "เวลา") -> time | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise FormError(f"{field}ไม่ถูกต้อง: ใช้รูปแบบ HH:MM") from None


def parse_int_in_range(
    value: str, low: int, high: int, *, field: str = "ตัวเลข"
) -> int:
    try:
        number = int(value)
    except ValueError:
        raise FormError(f"{field}ไม่ถูกต้อง: กรุณากรอกตัวเลข") from None
    if not low <= number <= high:
        raise FormError(f"{field}ต้องอยู่ระหว่าง {low} ถึง {high}")
    return number
