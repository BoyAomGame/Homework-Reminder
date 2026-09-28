from datetime import date, time, timedelta

from app import bot_messages, timeutil
from app.db.session import SessionFactory
from app.llm.base import ParsedAssignment, extract_json_object, LLMError
from app.parser import Clarification, ParsedFields, parse_assignment_text
from app.services import timetable as tts

import pytest

from tests.fakes import FakeTextLLM, always_failing_llm


async def _with_math_on_friday(user):
    async with SessionFactory() as session:
        await tts.upsert(
            session, user.id, day_of_week=4, period_number=1,
            subject_name="คณิตศาสตร์", teacher_name="ครูสมชาย", room="301",
        )


def _next_friday() -> date:
    today = timeutil.now_bangkok().date()
    return today + timedelta(days=(4 - today.weekday()) % 7 or 7)


async def test_autofill_teacher_room_and_period_time(user):
    await _with_math_on_friday(user)
    llm = FakeTextLLM(ParsedAssignment(
        subject_name="คณิตศาสตร์", location="หน้า 5", due_date=_next_friday(),
    ))
    async with SessionFactory() as session:
        result = await parse_assignment_text(session, user, "การบ้านเลข หน้า 5 ส่งวันศุกร์", llm)
    assert isinstance(result, ParsedFields)
    assert result.teacher_name == "ครูสมชาย"      # from timetable
    assert result.location == "หน้า 5"            # user's text wins over room
    assert result.due_time == time(8, 30)         # period 1 default start
    assert result.time_from_timetable

    # known subjects were passed to the model for normalization
    assert "คณิตศาสตร์" in llm.contexts[0]


async def test_missing_due_date_asks_instead_of_guessing(user):
    llm = FakeTextLLM(ParsedAssignment(subject_name="อังกฤษ"))
    async with SessionFactory() as session:
        result = await parse_assignment_text(session, user, "การบ้านอังกฤษ", llm)
    assert isinstance(result, Clarification)
    assert result.question == bot_messages.CLARIFY_DUE_DATE


async def test_missing_subject_asks(user):
    llm = FakeTextLLM(ParsedAssignment(due_date=date(2026, 7, 20)))
    async with SessionFactory() as session:
        result = await parse_assignment_text(session, user, "ส่งวันจันทร์", llm)
    assert isinstance(result, Clarification)
    assert result.question == bot_messages.CLARIFY_SUBJECT


async def test_malformed_llm_output_retries_then_asks(user):
    llm = always_failing_llm()
    async with SessionFactory() as session:
        result = await parse_assignment_text(session, user, "อะไรสักอย่าง", llm)
    assert isinstance(result, Clarification)
    assert len(llm.calls) == 2  # one retry, then gave up gracefully


async def test_next_class_resolves_to_next_meeting(user):
    await _with_math_on_friday(user)
    llm = FakeTextLLM(ParsedAssignment(subject_name="คณิตศาสตร์", date_hint="next_class"))
    async with SessionFactory() as session:
        result = await parse_assignment_text(session, user, "เลขส่งคาบหน้า", llm)
    assert isinstance(result, ParsedFields)
    assert result.due_date.weekday() == 4
    assert result.due_date > timeutil.now_bangkok().date() or (
        result.due_date == timeutil.now_bangkok().date()
        and timeutil.now_bangkok().time() < time(8, 30)
    )
    assert result.due_time == time(8, 30)


async def test_bare_date_moves_to_meeting_on_or_after(user):
    await _with_math_on_friday(user)
    friday = _next_friday()
    monday_before = friday - timedelta(days=4)
    llm = FakeTextLLM(ParsedAssignment(subject_name="คณิตศาสตร์", due_date=monday_before))
    async with SessionFactory() as session:
        result = await parse_assignment_text(session, user, "เลข ส่งวันจันทร์", llm)
    assert isinstance(result, ParsedFields)
    assert result.due_date == friday
    assert result.date_moved_to_meeting


async def test_no_timetable_match_leaves_time_null(user):
    llm = FakeTextLLM(ParsedAssignment(subject_name="พละ", due_date=date(2026, 7, 22)))
    async with SessionFactory() as session:
        result = await parse_assignment_text(session, user, "พละ ส่งพุธ", llm)
    assert isinstance(result, ParsedFields)
    assert result.due_time is None
    assert not result.time_from_timetable


def test_extract_json_tolerates_fences_and_prose():
    raw = 'Here you go:\n```json\n{"subject_name": "เลข"}\n```'
    assert extract_json_object(raw) == {"subject_name": "เลข"}
    with pytest.raises(LLMError):
        extract_json_object("no json here")
    with pytest.raises(LLMError):
        extract_json_object('["a", "list"]')
