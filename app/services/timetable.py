"""Timetable CRUD and the lookups behind assignment auto-fill.

Period times are per-user data. When a user has not set an explicit
start time for an entry, a fallback default applies: period 1 starts at
08:30 and each period lasts 50 minutes.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TimetableEntry

# Fallback defaults, used only when the user hasn't set their own times.
DEFAULT_FIRST_PERIOD_START = time(8, 30)
DEFAULT_PERIOD_MINUTES = 50

# How far ahead to look when resolving "next class" / meeting-on-date.
MAX_LOOKAHEAD_DAYS = 14


def default_start_time(period_number: int) -> time:
    """08:30 for period 1, then +50 minutes per period."""
    base = datetime.combine(
        date(2000, 1, 1), DEFAULT_FIRST_PERIOD_START
    ) + timedelta(minutes=DEFAULT_PERIOD_MINUTES * (period_number - 1))
    return base.time()


def effective_start_time(entry: TimetableEntry) -> time:
    return entry.start_time or default_start_time(entry.period_number)


# --- CRUD --------------------------------------------------------------------

async def list_for_user(session: AsyncSession, user_id: int) -> list[TimetableEntry]:
    return list(
        await session.scalars(
            select(TimetableEntry)
            .where(TimetableEntry.user_id == user_id)
            .order_by(TimetableEntry.day_of_week, TimetableEntry.period_number)
        )
    )


async def get_for_user(
    session: AsyncSession, user_id: int, entry_id: int
) -> TimetableEntry | None:
    return await session.scalar(
        select(TimetableEntry).where(
            TimetableEntry.id == entry_id, TimetableEntry.user_id == user_id
        )
    )


async def upsert(
    session: AsyncSession,
    user_id: int,
    *,
    day_of_week: int,
    period_number: int,
    subject_name: str,
    teacher_name: str | None = None,
    room: str | None = None,
    start_time: time | None = None,
    end_time: time | None = None,
) -> TimetableEntry:
    """Create or overwrite the entry in a (day, period) slot."""
    entry = await session.scalar(
        select(TimetableEntry).where(
            TimetableEntry.user_id == user_id,
            TimetableEntry.day_of_week == day_of_week,
            TimetableEntry.period_number == period_number,
        )
    )
    if entry is None:
        entry = TimetableEntry(
            user_id=user_id, day_of_week=day_of_week, period_number=period_number
        )
        session.add(entry)
    entry.subject_name = subject_name
    entry.teacher_name = teacher_name
    entry.room = room
    entry.start_time = start_time
    entry.end_time = end_time
    await session.commit()
    return entry


async def delete(session: AsyncSession, entry: TimetableEntry) -> None:
    await session.delete(entry)
    await session.commit()


async def replace_all(
    session: AsyncSession, user_id: int, rows: list[dict]
) -> list[TimetableEntry]:
    """Replace the whole timetable — used by the image-import confirm step."""
    await session.execute(
        sql_delete(TimetableEntry).where(TimetableEntry.user_id == user_id)
    )
    entries = [TimetableEntry(user_id=user_id, **row) for row in rows]
    session.add_all(entries)
    await session.commit()
    return entries


# --- Auto-fill lookups --------------------------------------------------------

def match_subject(
    entries: list[TimetableEntry], subject_name: str
) -> list[TimetableEntry]:
    """Entries whose subject matches, case-insensitively.

    Exact matches win; otherwise fall back to substring containment in
    either direction so "เลข" matches "คณิตศาสตร์ (เลข)" and vice versa.
    """
    wanted = subject_name.strip().casefold()
    if not wanted:
        return []
    exact = [e for e in entries if e.subject_name.strip().casefold() == wanted]
    if exact:
        return exact
    return [
        e for e in entries
        if wanted in e.subject_name.casefold()
        or e.subject_name.strip().casefold() in wanted
    ]


def _earliest_period_on(
    entries: list[TimetableEntry], day_of_week: int
) -> TimetableEntry | None:
    on_day = [e for e in entries if e.day_of_week == day_of_week]
    return min(on_day, key=effective_start_time, default=None)


def meeting_on_or_after(
    entries: list[TimetableEntry], from_date: date
) -> tuple[date, time] | None:
    """First class meeting on ``from_date`` or within the lookahead window."""
    for offset in range(MAX_LOOKAHEAD_DAYS):
        day = from_date + timedelta(days=offset)
        entry = _earliest_period_on(entries, day.weekday())
        if entry is not None:
            return day, effective_start_time(entry)
    return None


def next_meeting(
    entries: list[TimetableEntry], after: datetime
) -> tuple[date, time] | None:
    """First class meeting strictly after the (Bangkok-local) moment ``after``."""
    for offset in range(MAX_LOOKAHEAD_DAYS):
        day = after.date() + timedelta(days=offset)
        candidates = [e for e in entries if e.day_of_week == day.weekday()]
        if offset == 0:  # skip today's meetings that already started
            candidates = [
                e for e in candidates if effective_start_time(e) > after.time()
            ]
        if candidates:
            return day, effective_start_time(min(candidates, key=effective_start_time))
    return None
