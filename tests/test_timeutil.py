from datetime import date, datetime, time, timezone

from app import timeutil


def test_deadline_with_time_converts_bangkok_to_utc():
    # 08:30 Bangkok = 01:30 UTC (UTC+7)
    deadline = timeutil.deadline_utc(date(2026, 6, 20), time(8, 30))
    assert deadline == datetime(2026, 6, 20, 1, 30, tzinfo=timezone.utc)


def test_deadline_without_time_is_end_of_day_bangkok():
    deadline = timeutil.deadline_utc(date(2026, 6, 20), None)
    assert deadline == datetime(2026, 6, 20, 16, 59, tzinfo=timezone.utc)


def test_thai_datetime_formatting():
    # 2026-06-20 is a Saturday; the spec example date 2026-06-19 is a Friday
    assert timeutil.thai_datetime(date(2026, 6, 19), time(8, 30)) == "ศุกร์ 19 มิ.ย. 08:30 น."
    assert timeutil.thai_datetime(date(2026, 6, 19), None) == "ศุกร์ 19 มิ.ย."


def test_thai_offset_units():
    assert timeutil.thai_offset(1440) == "1 วัน"
    assert timeutil.thai_offset(180) == "3 ชม."
    assert timeutil.thai_offset(45) == "45 นาที"
    assert timeutil.thai_offset(0) == "เมื่อถึงกำหนด"


def test_ensure_utc_interprets_naive_as_utc():
    naive = datetime(2026, 1, 1, 12, 0)
    assert timeutil.ensure_utc(naive).tzinfo == timezone.utc
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert timeutil.ensure_utc(aware) is aware
