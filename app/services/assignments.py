"""Assignment CRUD, always scoped to a single user.

Every function takes an explicit ``user_id`` (or an assignment already
fetched through one), so there is no path that reads or writes another
user's data. Deadline-affecting changes reschedule reminders here, so
the web and Discord edit paths cannot forget to do it.
"""

from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import runtime, timeutil
from app.db.models import Assignment, AssignmentStatus

# Fields a user may change when editing an assignment.
EDITABLE_FIELDS = {
    "subject_name", "teacher_name", "location",
    "due_date", "due_time", "notes",
}
_DEADLINE_FIELDS = {"due_date", "due_time"}


async def list_for_user(
    session: AsyncSession, user_id: int, *, include_done: bool = True
) -> list[Assignment]:
    query = (
        select(Assignment)
        .where(Assignment.user_id == user_id)
        .order_by(Assignment.due_date, Assignment.due_time.nulls_last(), Assignment.id)
    )
    if not include_done:
        query = query.where(Assignment.status == AssignmentStatus.PENDING)
    return list(await session.scalars(query))


async def get_for_user(
    session: AsyncSession, user_id: int, assignment_id: int
) -> Assignment | None:
    return await session.scalar(
        select(Assignment).where(
            Assignment.id == assignment_id, Assignment.user_id == user_id
        )
    )


async def create(
    session: AsyncSession,
    user_id: int,
    *,
    subject_name: str,
    due_date: date,
    due_time: time | None = None,
    teacher_name: str | None = None,
    location: str | None = None,
    notes: str | None = None,
) -> Assignment:
    assignment = Assignment(
        user_id=user_id,
        subject_name=subject_name,
        due_date=due_date,
        due_time=due_time,
        teacher_name=teacher_name,
        location=location,
        notes=notes,
        reminders_sent=[],
    )
    session.add(assignment)
    await session.commit()
    _reschedule(assignment)
    return assignment


async def update(
    session: AsyncSession, assignment: Assignment, changes: dict
) -> Assignment:
    """Apply edits; reminder jobs are rebuilt when the deadline moved."""
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")

    deadline_changed = False
    for field, value in changes.items():
        if getattr(assignment, field) != value:
            setattr(assignment, field, value)
            deadline_changed |= field in _DEADLINE_FIELDS

    if deadline_changed:
        # The old fired-reminder state belongs to the old deadline.
        assignment.reminders_sent = []
        assignment.overdue_warned_at = None
        assignment.overdue_warn_count = 0
    await session.commit()
    if deadline_changed:
        _reschedule(assignment)
    return assignment


async def set_done(
    session: AsyncSession, assignment: Assignment, done: bool
) -> Assignment:
    assignment.status = AssignmentStatus.DONE if done else AssignmentStatus.PENDING
    assignment.completed_at = timeutil.now_utc() if done else None
    await session.commit()
    # Done assignments need no reminders; reopened ones need them again.
    _reschedule(assignment)
    return assignment


async def delete(session: AsyncSession, assignment: Assignment) -> None:
    _unschedule(assignment)
    await session.delete(assignment)
    await session.commit()


def is_overdue(assignment: Assignment) -> bool:
    return (
        assignment.status == AssignmentStatus.PENDING
        and timeutil.deadline_utc(assignment.due_date, assignment.due_time)
        < timeutil.now_utc()
    )


def _reschedule(assignment: Assignment) -> None:
    scheduler = runtime.scheduler()
    if scheduler is not None:
        scheduler.reschedule_assignment(assignment)


def _unschedule(assignment: Assignment) -> None:
    scheduler = runtime.scheduler()
    if scheduler is not None:
        scheduler.unschedule_assignment(assignment.id)
