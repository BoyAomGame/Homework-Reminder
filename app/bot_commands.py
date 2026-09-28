"""Handle one incoming DM: an LLM decides which command the user intends.

Every DM is read by the intent router (app/providers -> TextLLM.detect_intent),
which classifies it into add / list / done / delete / edit / help and resolves
free-text references ("the chemistry one") to a concrete assignment id using the
user's pending list. Only a delete is confirmed before it runs; everything else
executes immediately.

All replies come from app.bot_messages and are in Thai. The bot manager gives us
the web-account user id the message belongs to; every database touch goes through
the user-scoped service layer.
"""

import logging

from sqlalchemy import select

from app import bot_messages
from app.db.models import Assignment, User
from app.db.session import SessionFactory
from app.llm.base import DetectedIntent, LLMError, TextLLM
from app.llm.prompts import assignment_prompt_context, render_pending
from app.parser import Clarification, complete_assignment
from app.providers.registry import get_text_llm
from app.services import assignments as assignments_service
from app.web.forms import FormError, parse_date, parse_optional_time
from app import timeutil

logger = logging.getLogger(__name__)

INTENT_ATTEMPTS = 2  # one retry on malformed model output

# Deterministic replies to a pending delete confirmation (the model is not
# consulted for the yes/no step).
CONFIRM_WORDS = {"ใช่", "ยืนยัน", "yes", "y", "ok", "โอเค", "confirm"}
CANCEL_WORDS = {"ไม่", "ยกเลิก", "no", "n", "cancel"}

# Router edit-field names -> Assignment column names.
EDIT_FIELD_COLUMNS = {
    "subject": "subject_name",
    "teacher": "teacher_name",
    "location": "location",
    "date": "due_date",
    "time": "due_time",
    "notes": "notes",
}

# user_id -> assignment_id awaiting a "yes" before it is deleted. In-memory and
# per-process, which is fine: DMs for one user arrive sequentially on the app's
# single event loop, and a lost confirmation just means the user re-asks.
_pending_delete: dict[int, int] = {}


async def handle_dm(user_id: int, text: str, llm: TextLLM | None = None) -> str:
    """Route one DM through the intent router (or the delete-confirm gate)."""
    text = text.strip()
    if not text:
        return bot_messages.HELP
    try:
        return await _dispatch(user_id, text, llm or get_text_llm())
    except Exception:
        logger.exception("DM handling failed for user %d", user_id)
        return bot_messages.ERROR_GENERIC


async def _dispatch(user_id: int, text: str, llm: TextLLM) -> str:
    # A pending delete takes priority: this reply is its yes/no answer.
    if user_id in _pending_delete:
        resolved = await _resolve_pending_delete(user_id, text)
        if resolved is not None:
            return resolved
        # Not a yes/no — drop the pending delete and treat as a fresh message.

    intent = await _detect_intent(user_id, text, llm)
    if intent is None:
        return bot_messages.CLARIFY_UNPARSEABLE
    return await _route(user_id, intent)


async def _resolve_pending_delete(user_id: int, text: str) -> str | None:
    """Handle the yes/no answer to a pending delete; None if it was neither."""
    answer = text.casefold()
    if answer in CONFIRM_WORDS:
        assignment_id = _pending_delete.pop(user_id)
        return await _delete(user_id, assignment_id)
    if answer in CANCEL_WORDS:
        _pending_delete.pop(user_id)
        return bot_messages.DELETE_CANCELLED
    _pending_delete.pop(user_id)
    return None


async def _detect_intent(
    user_id: int, text: str, llm: TextLLM
) -> DetectedIntent | None:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        if user is None:
            return None
        pending = await assignments_service.list_for_user(
            session, user_id, include_done=False
        )
        known_subjects = sorted({a.subject_name for a in pending})
        context = assignment_prompt_context(timeutil.now_bangkok(), known_subjects)
        rendered = render_pending(pending)

    for attempt in range(1, INTENT_ATTEMPTS + 1):
        try:
            return await llm.detect_intent(
                text, prompt_context=context, pending=rendered
            )
        except LLMError as exc:
            logger.warning(
                "Intent detection attempt %d/%d failed: %s",
                attempt, INTENT_ATTEMPTS, exc,
            )
    return None


async def _route(user_id: int, intent: DetectedIntent) -> str:
    command = intent.command
    if command == "help":
        return bot_messages.HELP
    if command == "list":
        return await _list_pending(user_id)
    if command == "done":
        if intent.assignment_id is None:
            return bot_messages.CLARIFY_WHICH
        return await _mark_done(user_id, intent.assignment_id)
    if command == "delete":
        if intent.assignment_id is None:
            return bot_messages.CLARIFY_WHICH
        return await _request_delete(user_id, intent.assignment_id)
    if command == "edit":
        return await _edit(user_id, intent)
    if command == "add":
        return await _add(user_id, intent)
    return bot_messages.CLARIFY_UNPARSEABLE


async def _get_owned(session, user_id: int, assignment_id: int) -> Assignment | None:
    return await assignments_service.get_for_user(session, user_id, assignment_id)


async def _list_pending(user_id: int) -> str:
    async with SessionFactory() as session:
        pending = await assignments_service.list_for_user(
            session, user_id, include_done=False
        )
        return bot_messages.pending_list(pending, assignments_service.is_overdue)


async def _mark_done(user_id: int, assignment_id: int) -> str:
    async with SessionFactory() as session:
        assignment = await _get_owned(session, user_id, assignment_id)
        if assignment is None:
            return bot_messages.NOT_FOUND
        await assignments_service.set_done(session, assignment, True)
        return bot_messages.marked_done(assignment)


async def _request_delete(user_id: int, assignment_id: int) -> str:
    """Confirm before deleting: stash the target and ask for a yes/no."""
    async with SessionFactory() as session:
        assignment = await _get_owned(session, user_id, assignment_id)
        if assignment is None:
            return bot_messages.NOT_FOUND
        _pending_delete[user_id] = assignment.id
        return bot_messages.confirm_delete(assignment)


async def _delete(user_id: int, assignment_id: int) -> str:
    async with SessionFactory() as session:
        assignment = await _get_owned(session, user_id, assignment_id)
        if assignment is None:
            return bot_messages.NOT_FOUND
        await assignments_service.delete(session, assignment)
        return bot_messages.deleted(assignment)


async def _edit(user_id: int, intent: DetectedIntent) -> str:
    if intent.assignment_id is None:
        return bot_messages.CLARIFY_WHICH
    column = EDIT_FIELD_COLUMNS.get(intent.edit_field or "")
    if column is None or not intent.edit_value:
        return bot_messages.EDIT_USAGE
    value = intent.edit_value.strip()

    try:
        if column == "due_date":
            value = parse_date(value)
        elif column == "due_time":
            value = parse_optional_time(value)
    except FormError:
        return (
            bot_messages.EDIT_BAD_DATE if column == "due_date" else bot_messages.EDIT_BAD_TIME
        )

    async with SessionFactory() as session:
        assignment = await _get_owned(session, user_id, intent.assignment_id)
        if assignment is None:
            return bot_messages.NOT_FOUND
        await assignments_service.update(session, assignment, {column: value})
        return bot_messages.edited(assignment)


async def _add(user_id: int, intent: DetectedIntent) -> str:
    if intent.assignment is None:
        return bot_messages.CLARIFY_UNPARSEABLE
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        if user is None:
            return bot_messages.ERROR_GENERIC

        result = await complete_assignment(session, user, intent.assignment)
        if isinstance(result, Clarification):
            return result.question

        assignment = await assignments_service.create(
            session,
            user.id,
            subject_name=result.subject_name,
            due_date=result.due_date,
            due_time=result.due_time,
            teacher_name=result.teacher_name,
            location=result.location,
            notes=result.notes,
        )
        return bot_messages.confirmation(
            assignment,
            user.reminder_offsets,
            time_from_timetable=result.time_from_timetable,
            date_moved_to_meeting=result.date_moved_to_meeting,
        )
