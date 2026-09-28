from datetime import date, datetime, time

from app.db.models import TimetableEntry
from app.db.session import SessionFactory
from app.services import timetable as tts
from app.timeutil import BANGKOK


def entry(day: int, period: int, subject: str, start: time | None = None) -> TimetableEntry:
    return TimetableEntry(
        user_id=1, day_of_week=day, period_number=period,
        subject_name=subject, start_time=start,
    )


def test_default_period_times():
    assert tts.default_start_time(1) == time(8, 30)
    assert tts.default_start_time(2) == time(9, 20)
    assert tts.default_start_time(4) == time(11, 0)


def test_effective_start_prefers_user_time():
    assert tts.effective_start_time(entry(0, 1, "เลข", time(7, 45))) == time(7, 45)
    assert tts.effective_start_time(entry(0, 3, "เลข")) == time(10, 10)


def test_match_subject_exact_beats_substring():
    entries = [entry(0, 1, "คณิตศาสตร์"), entry(1, 1, "คณิตศาสตร์เพิ่มเติม")]
    assert tts.match_subject(entries, "คณิตศาสตร์") == [entries[0]]
    # substring fallback works in both directions, case-insensitively
    assert tts.match_subject(entries, "คณิต") == entries
    assert tts.match_subject([entry(0, 1, "English")], "english reading") != []
    assert tts.match_subject(entries, "ประวัติศาสตร์") == []
    assert tts.match_subject(entries, "") == []


def test_meeting_on_or_after_same_day_and_lookahead():
    entries = [entry(4, 1, "เลข")]  # Fridays, default 08:30
    friday = date(2026, 7, 17)
    assert tts.meeting_on_or_after(entries, friday) == (friday, time(8, 30))
    # a Monday date rolls forward to that week's Friday
    assert tts.meeting_on_or_after(entries, date(2026, 7, 13)) == (friday, time(8, 30))
    assert tts.meeting_on_or_after([], friday) is None


def test_next_meeting_skips_todays_started_class():
    entries = [entry(4, 1, "เลข"), entry(4, 5, "เลข")]  # Friday 08:30 and 11:50
    friday_morning = datetime(2026, 7, 17, 9, 0, tzinfo=BANGKOK)
    # 08:30 already started -> same day's later period wins
    assert tts.next_meeting(entries, friday_morning) == (date(2026, 7, 17), time(11, 50))
    friday_evening = datetime(2026, 7, 17, 18, 0, tzinfo=BANGKOK)
    assert tts.next_meeting(entries, friday_evening) == (date(2026, 7, 24), time(8, 30))


async def test_upsert_overwrites_slot_and_replace_all(user):
    async with SessionFactory() as session:
        await tts.upsert(session, user.id, day_of_week=0, period_number=1,
                         subject_name="เลข")
        await tts.upsert(session, user.id, day_of_week=0, period_number=1,
                         subject_name="อังกฤษ", teacher_name="ครูแนน")
        entries = await tts.list_for_user(session, user.id)
        assert len(entries) == 1
        assert entries[0].subject_name == "อังกฤษ"

        await tts.replace_all(session, user.id, [
            {"day_of_week": 1, "period_number": 2, "subject_name": "ฟิสิกส์",
             "teacher_name": None, "room": "301", "start_time": None, "end_time": None},
        ])
        entries = await tts.list_for_user(session, user.id)
        assert [e.subject_name for e in entries] == ["ฟิสิกส์"]
