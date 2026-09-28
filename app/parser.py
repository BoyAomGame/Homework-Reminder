"""Turn a free-text DM into validated, timetable-completed assignment fields.

The pipeline is: text LLM -> schema validation -> timetable auto-fill.
The result is either ``ParsedFields`` ready to store, or a
``Clarification`` holding a Thai question to send back — we never guess
a missing due date.
"""

import logging
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from app import bot_messages, timeutil
from app.db.models import User
from app.llm.base import LLMError, ParsedAssignment, TextLLM
from app.llm.prompts import assignment_prompt_context
from app.services import timetable as timetable_service

logger = logging.getLogger(__name__)

LLM_ATTEMPTS = 2  # one retry on malformed model output


@dataclass
class ParsedFields:
    """Validated assignment fields plus notes about what was inferred."""

    subject_name: str
    due_date: date
    due_time: time | None
    teacher_name: str | None
    location: str | None
    notes: str | None
    time_from_timetable: bool = False   # due_time came from a period lookup
    date_moved_to_meeting: bool = False  # date advanced to the subject's next class


@dataclass
class Clarification:
    """We could not extract enough; ask the user this (Thai) question."""

    question: str


async def parse_assignment_text(
    session: AsyncSession, user: User, text: str, llm: TextLLM
) -> ParsedFields | Clarification:
    entries = await timetable_service.list_for_user(session, user.id)
    known_subjects = sorted({e.subject_name for e in entries})
    context = assignment_prompt_context(timeutil.now_bangkok(), known_subjects)

    parsed = await _call_llm(llm, text, context)
    if parsed is None:
        return Clarification(bot_messages.CLARIFY_UNPARSEABLE)

    return await complete_assignment(session, user, parsed)


async def complete_assignment(
    session: AsyncSession, user: User, parsed: ParsedAssignment
) -> ParsedFields | Clarification:
    """Validate extracted fields and auto-fill from the timetable.

    Shared by the natural-language add path and the intent router, which
    both arrive here with an already-extracted ``ParsedAssignment``. We
    never guess a missing subject or due date — we return a ``Clarification``.
    """
    if not parsed.subject_name:
        return Clarification(bot_messages.CLARIFY_SUBJECT)
    entries = await timetable_service.list_for_user(session, user.id)
    return _fill_from_timetable(parsed, entries)


async def _call_llm(
    llm: TextLLM, text: str, context: str
) -> ParsedAssignment | None:
    for attempt in range(1, LLM_ATTEMPTS + 1):
        try:
            return await llm.parse_assignment(text, prompt_context=context)
        except LLMError as exc:
            logger.warning("Text LLM attempt %d/%d failed: %s", attempt, LLM_ATTEMPTS, exc)
    return None


def _fill_from_timetable(
    parsed: ParsedAssignment,
    entries: list,
) -> ParsedFields | Clarification:
    matching = timetable_service.match_subject(entries, parsed.subject_name)

    teacher = parsed.teacher_name or next(
        (e.teacher_name for e in matching if e.teacher_name), None
    )
    location = parsed.location or next((e.room for e in matching if e.room), None)

    due_date = parsed.due_date
    due_time = parsed.due_time
    time_from_timetable = False
    date_moved = False

    if parsed.date_hint == "next_class" and matching:
        # "ส่งคาบหน้า": deadline is the subject's next meeting from now.
        meeting = timetable_service.next_meeting(matching, timeutil.now_bangkok())
        if meeting is not None:
            due_date, due_time = meeting
            time_from_timetable = True
    elif due_date is not None and due_time is None and matching:
        # Bare date: deadline is the subject's meeting on/after that date.
        meeting = timetable_service.meeting_on_or_after(matching, due_date)
        if meeting is not None:
            date_moved = meeting[0] != due_date
            due_date, due_time = meeting
            time_from_timetable = True

    if due_date is None:
        # Never guess a deadline — ask instead.
        return Clarification(bot_messages.CLARIFY_DUE_DATE)

    return ParsedFields(
        subject_name=parsed.subject_name,
        due_date=due_date,
        due_time=due_time,
        teacher_name=teacher,
        location=location,
        notes=parsed.notes,
        time_from_timetable=time_from_timetable,
        date_moved_to_meeting=date_moved,
    )
