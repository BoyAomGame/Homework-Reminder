"""Reminder and overdue-warning scheduling.

Jobs live in an in-memory APScheduler and are rebuilt from the database
on startup: which reminders already fired is stored on each assignment
row (``reminders_sent``, ``overdue_warned_at``), so a restart neither
double-sends nor loses anything.

Per assignment, one date job per reminder offset still in the future
(job id ``reminder:{assignment_id}:{offset}``). A periodic sweep DMs
overdue warnings for pending assignments past their deadline — at most
once a day per assignment and three times total, so it never spams.

Delivery uses the owner's own Discord bot. If that bot is down the
reminder is NOT marked sent: a short retry is scheduled, and a restart
reloads it, so the reminder survives outages.
"""

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app import bot_messages, runtime, timeutil
from app.config import get_settings
from app.db.models import Assignment, AssignmentStatus, User
from app.db.session import SessionFactory

logger = logging.getLogger(__name__)

RETRY_DELAY = timedelta(minutes=10)
OVERDUE_REPEAT_EVERY = timedelta(hours=24)
OVERDUE_MAX_WARNINGS = 3
OVERDUE_JOB_ID = "overdue-sweep"


class ReminderScheduler:
    def __init__(self):
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    # --- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        self._scheduler.start()
        self._scheduler.add_job(
            self._overdue_sweep,
            "interval",
            minutes=get_settings().overdue_check_interval_minutes,
            id=OVERDUE_JOB_ID,
        )
        await self._reload_from_db()

    async def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def _reload_from_db(self) -> None:
        """Rebuild reminder jobs for every pending assignment after a restart."""
        async with SessionFactory() as session:
            pending = await session.scalars(
                select(Assignment).where(
                    Assignment.status == AssignmentStatus.PENDING
                )
            )
            count = 0
            for assignment in pending:
                offsets = await self._offsets_for(session, assignment.user_id)
                self._add_reminder_jobs(assignment, offsets)
                count += 1
        logger.info("Scheduler reloaded %d pending assignment(s)", count)

    # --- public API (called from services and web routes) ----------------------

    def reschedule_assignment(self, assignment: Assignment) -> None:
        """Rebuild one assignment's jobs after any create/edit/status change."""
        # Queued as an immediate job so (sync) callers don't need to await us.
        self._scheduler.add_job(self._reschedule_by_id, args=[assignment.id])

    def unschedule_assignment(self, assignment_id: int) -> None:
        self._remove_reminder_jobs(assignment_id)

    def reschedule_user(self, user_id: int) -> None:
        """Rebuild all of a user's jobs — used when reminder offsets change."""
        self._scheduler.add_job(self._reschedule_user, args=[user_id])

    # --- job construction -------------------------------------------------------

    async def _reschedule_by_id(self, assignment_id: int) -> None:
        self._remove_reminder_jobs(assignment_id)
        async with SessionFactory() as session:
            assignment = await session.get(Assignment, assignment_id)
            if assignment is None or assignment.status != AssignmentStatus.PENDING:
                return
            offsets = await self._offsets_for(session, assignment.user_id)
            self._add_reminder_jobs(assignment, offsets)

    async def _reschedule_user(self, user_id: int) -> None:
        async with SessionFactory() as session:
            pending = list(
                await session.scalars(
                    select(Assignment).where(
                        Assignment.user_id == user_id,
                        Assignment.status == AssignmentStatus.PENDING,
                    )
                )
            )
            offsets = await self._offsets_for(session, user_id)
        for assignment in pending:
            self._remove_reminder_jobs(assignment.id)
            self._add_reminder_jobs(assignment, offsets)

    async def _offsets_for(self, session, user_id: int) -> list[int]:
        user = await session.get(User, user_id)
        offsets = user.reminder_offsets if user else None
        return offsets or list(get_settings().default_reminder_offsets)

    def _add_reminder_jobs(self, assignment: Assignment, offsets: list[int]) -> None:
        """One date job per offset that hasn't fired and is still ahead of us.

        Offsets whose moment already passed are skipped: firing a
        "1 day left" reminder two hours before the deadline would lie,
        and anything past the deadline is the overdue sweep's job.
        """
        deadline = timeutil.deadline_utc(assignment.due_date, assignment.due_time)
        now = timeutil.now_utc()
        for offset in offsets:
            if offset in (assignment.reminders_sent or []):
                continue
            run_at = deadline - timedelta(minutes=offset)
            if run_at <= now:
                continue
            self._scheduler.add_job(
                self._fire_reminder,
                "date",
                run_date=run_at,
                id=self._job_id(assignment.id, offset),
                args=[assignment.id, offset],
                replace_existing=True,
                misfire_grace_time=3600,
            )

    @staticmethod
    def _job_id(assignment_id: int, offset: int) -> str:
        return f"reminder:{assignment_id}:{offset}"

    def _remove_reminder_jobs(self, assignment_id: int) -> None:
        prefix = f"reminder:{assignment_id}:"
        for job in self._scheduler.get_jobs():
            if job.id.startswith(prefix):
                job.remove()

    # --- firing ------------------------------------------------------------------

    async def _fire_reminder(self, assignment_id: int, offset: int) -> None:
        async with SessionFactory() as session:
            assignment = await session.get(Assignment, assignment_id)
            if (
                assignment is None
                or assignment.status != AssignmentStatus.PENDING
                or offset in (assignment.reminders_sent or [])
            ):
                return

            delivered = await self._send_dm(
                assignment.user_id, bot_messages.reminder(assignment, offset)
            )
            if delivered:
                # New list (not append): JSON columns only detect reassignment.
                assignment.reminders_sent = [*(assignment.reminders_sent or []), offset]
                await session.commit()
                return

        # Bot down or not linked: retry shortly; a restart also re-schedules
        # because the offset was never marked sent.
        logger.warning(
            "Reminder %s for assignment %d undelivered; retrying in %s",
            offset, assignment_id, RETRY_DELAY,
        )
        self._scheduler.add_job(
            self._fire_reminder,
            "date",
            run_date=timeutil.now_utc() + RETRY_DELAY,
            id=self._job_id(assignment_id, offset),
            args=[assignment_id, offset],
            replace_existing=True,
            misfire_grace_time=3600,
        )

    async def _overdue_sweep(self) -> None:
        """DM overdue warnings: once when newly overdue, then daily, max 3."""
        now = timeutil.now_utc()
        async with SessionFactory() as session:
            pending = await session.scalars(
                select(Assignment).where(
                    Assignment.status == AssignmentStatus.PENDING,
                    Assignment.overdue_warn_count < OVERDUE_MAX_WARNINGS,
                )
            )
            for assignment in pending:
                deadline = timeutil.deadline_utc(
                    assignment.due_date, assignment.due_time
                )
                if deadline >= now:
                    continue
                last = assignment.overdue_warned_at
                if last is not None and now - timeutil.ensure_utc(last) < OVERDUE_REPEAT_EVERY:
                    continue
                delivered = await self._send_dm(
                    assignment.user_id, bot_messages.overdue_warning(assignment)
                )
                if delivered:
                    assignment.overdue_warned_at = now
                    assignment.overdue_warn_count += 1
                    await session.commit()

    @staticmethod
    async def _send_dm(user_id: int, text: str) -> bool:
        manager = runtime.bot_manager()
        if manager is None:
            return False
        return await manager.send_dm(user_id, text)
