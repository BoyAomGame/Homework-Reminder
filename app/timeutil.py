"""Timezone and Thai-language date helpers.

The database stores every datetime in UTC; users see Asia/Bangkok.
Everything that crosses that boundary goes through this module.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

BANGKOK = ZoneInfo("Asia/Bangkok")

END_OF_DAY = time(23, 59)

THAI_WEEKDAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]

THAI_MONTHS_ABBR = [
    "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_bangkok() -> datetime:
    return datetime.now(BANGKOK)


def ensure_utc(dt: datetime) -> datetime:
    """Interpret a naive datetime (e.g. from SQLite) as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def to_bangkok(dt: datetime) -> datetime:
    """Convert a stored (UTC) datetime to Bangkok time for display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BANGKOK)


def to_utc(dt: datetime) -> datetime:
    """Convert a Bangkok-local datetime to UTC for storage."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BANGKOK)
    return dt.astimezone(timezone.utc)


def deadline_utc(due_date: date, due_time: time | None) -> datetime:
    """Effective deadline of an assignment as a UTC datetime.

    When no due time is known, the deadline is the end of the due date
    (23:59 Bangkok time).
    """
    local = datetime.combine(due_date, due_time or END_OF_DAY, tzinfo=BANGKOK)
    return local.astimezone(timezone.utc)


def thai_date(d: date) -> str:
    """Format a date in Thai, e.g. ``ศุกร์ 20 มิ.ย.``"""
    return f"{THAI_WEEKDAYS[d.weekday()]} {d.day} {THAI_MONTHS_ABBR[d.month - 1]}"


def thai_datetime(due_date: date, due_time: time | None) -> str:
    """Format a due date (+ optional time) in Thai, e.g. ``ศุกร์ 20 มิ.ย. 08:30 น.``"""
    text = thai_date(due_date)
    if due_time is not None:
        text += f" {due_time.strftime('%H:%M')} น."
    return text


def thai_offset(minutes: int) -> str:
    """Describe a reminder offset in Thai, e.g. 1440 -> ``1 วัน``, 180 -> ``3 ชม.``"""
    if minutes == 0:
        return "เมื่อถึงกำหนด"
    if minutes % 1440 == 0:
        return f"{minutes // 1440} วัน"
    if minutes % 60 == 0:
        return f"{minutes // 60} ชม."
    return f"{minutes} นาที"


def offset_delta(minutes: int) -> timedelta:
    return timedelta(minutes=minutes)
