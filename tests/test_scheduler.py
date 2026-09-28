from datetime import date, datetime, time, timedelta, timezone

import pytest_asyncio

from app import runtime, timeutil
from app.db.models import Assignment, AssignmentStatus
from app.db.session import SessionFactory
from app.scheduler import OVERDUE_MAX_WARNINGS, ReminderScheduler
from app.services import assignments as asvc


class FakeBotManager:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.up = True

    async def send_dm(self, user_id: int, text: str) -> bool:
        if not self.up:
            return False
        self.sent.append((user_id, text))
        return True


@pytest_asyncio.fixture
async def scheduler():
    manager = FakeBotManager()
    runtime.set_bot_manager(manager)
    sched = ReminderScheduler()
    sched._scheduler.start()  # start without the overdue job / DB reload
    sched.manager = manager  # convenience handle for tests
    yield sched
    await sched.shutdown()
    runtime.set_bot_manager(None)


def _reminder_jobs(sched: ReminderScheduler, assignment_id: int) -> list[str]:
    prefix = f"reminder:{assignment_id}:"
    return sorted(j.id for j in sched._scheduler.get_jobs() if j.id.startswith(prefix))


async def _create(user, *, days_ahead=2, due_time=time(8, 30)) -> Assignment:
    async with SessionFactory() as session:
        return await asvc.create(
            session, user.id, subject_name="เลข",
            due_date=date.today() + timedelta(days=days_ahead), due_time=due_time,
        )


async def test_schedules_future_offsets_and_skips_sent(user, scheduler):
    assignment = await _create(user, days_ahead=2)
    async with SessionFactory() as session:
        row = await session.get(Assignment, assignment.id)
        row.reminders_sent = [1440]  # the 1-day reminder already fired
        await session.commit()
    await scheduler._reschedule_by_id(assignment.id)
    jobs = _reminder_jobs(scheduler, assignment.id)
    assert jobs == [f"reminder:{assignment.id}:0", f"reminder:{assignment.id}:180"]


async def test_skips_offsets_whose_moment_passed(user, scheduler):
    # Due in ~2 hours: the 1-day and 3-hour reminders are already in the past.
    soon = timeutil.now_bangkok() + timedelta(hours=2)
    async with SessionFactory() as session:
        assignment = await asvc.create(
            session, user.id, subject_name="เลข",
            due_date=soon.date(), due_time=soon.time().replace(microsecond=0),
        )
    await scheduler._reschedule_by_id(assignment.id)
    assert _reminder_jobs(scheduler, assignment.id) == [f"reminder:{assignment.id}:0"]


async def test_done_assignment_gets_no_jobs(user, scheduler):
    assignment = await _create(user)
    async with SessionFactory() as session:
        row = await session.get(Assignment, assignment.id)
        await asvc.set_done(session, row, True)
    await scheduler._reschedule_by_id(assignment.id)
    assert _reminder_jobs(scheduler, assignment.id) == []


async def test_fire_marks_sent_and_skips_duplicates(user, scheduler):
    assignment = await _create(user)
    await scheduler._fire_reminder(assignment.id, 180)
    assert len(scheduler.manager.sent) == 1
    assert "เลข" in scheduler.manager.sent[0][1]

    await scheduler._fire_reminder(assignment.id, 180)  # already recorded
    assert len(scheduler.manager.sent) == 1
    async with SessionFactory() as session:
        row = await session.get(Assignment, assignment.id)
        assert row.reminders_sent == [180]


async def test_failed_delivery_stays_unsent_and_retries(user, scheduler):
    assignment = await _create(user)
    scheduler.manager.up = False
    await scheduler._fire_reminder(assignment.id, 0)
    async with SessionFactory() as session:
        row = await session.get(Assignment, assignment.id)
        assert row.reminders_sent == []
    assert _reminder_jobs(scheduler, assignment.id) == [f"reminder:{assignment.id}:0"]


async def test_overdue_sweep_cadence_and_cap(user, scheduler):
    assignment = await _create(user, days_ahead=-1)  # already overdue
    await scheduler._overdue_sweep()
    assert len(scheduler.manager.sent) == 1
    assert "เลยกำหนดส่งแล้ว" in scheduler.manager.sent[0][1]

    await scheduler._overdue_sweep()  # < 24h since last warning
    assert len(scheduler.manager.sent) == 1

    async with SessionFactory() as session:  # pretend a day passed, thrice
        row = await session.get(Assignment, assignment.id)
        row.overdue_warned_at = datetime.now(timezone.utc) - timedelta(hours=25)
        row.overdue_warn_count = OVERDUE_MAX_WARNINGS - 1
        await session.commit()
    await scheduler._overdue_sweep()
    assert len(scheduler.manager.sent) == 2

    await scheduler._overdue_sweep()  # cap reached
    async with SessionFactory() as session:
        row = await session.get(Assignment, assignment.id)
        row.overdue_warned_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await session.commit()
    await scheduler._overdue_sweep()
    assert len(scheduler.manager.sent) == 2


async def test_done_assignments_never_warned(user, scheduler):
    assignment = await _create(user, days_ahead=-1)
    async with SessionFactory() as session:
        row = await session.get(Assignment, assignment.id)
        await asvc.set_done(session, row, True)
    await scheduler._overdue_sweep()
    assert scheduler.manager.sent == []
