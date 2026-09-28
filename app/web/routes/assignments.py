"""Assignment list, add/edit forms, status toggles, and deletion."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth.deps import CurrentUser, DbSession, verify_csrf
from app.db.models import Assignment, AssignmentStatus
from app.services import assignments as assignments_service
from app.web.forms import FormError, clean_text, parse_date, parse_optional_time
from app.web.templating import render

router = APIRouter(prefix="/assignments")


def _read_form(
    subject_name: str,
    due_date: str,
    due_time: str | None,
    teacher_name: str | None,
    location: str | None,
    notes: str | None,
) -> dict:
    subject = clean_text(subject_name, max_length=200)
    if not subject:
        raise FormError("กรุณากรอกชื่อวิชา")
    return {
        "subject_name": subject,
        "due_date": parse_date(due_date, field="วันที่ส่ง"),
        "due_time": parse_optional_time(due_time, field="เวลาส่ง"),
        "teacher_name": clean_text(teacher_name, max_length=200),
        "location": clean_text(location),
        "notes": clean_text(notes, max_length=2000),
    }


async def _owned_assignment(
    session: DbSession, user: CurrentUser, assignment_id: int
) -> Assignment:
    assignment = await assignments_service.get_for_user(session, user.id, assignment_id)
    if assignment is None:
        raise HTTPException(404, "ไม่พบการบ้าน")
    return assignment


@router.get("")
async def list_assignments(request: Request, user: CurrentUser, session: DbSession):
    items = await assignments_service.list_for_user(session, user.id)
    return render(
        request,
        "assignments/list.html",
        {
            "user": user,
            "assignments": items,
            "is_overdue": assignments_service.is_overdue,
            "AssignmentStatus": AssignmentStatus,
        },
    )


@router.get("/new")
async def new_form(request: Request, user: CurrentUser):
    return render(request, "assignments/form.html", {"user": user, "assignment": None})


@router.post("", dependencies=[Depends(verify_csrf)])
async def create(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    subject_name: Annotated[str, Form()],
    due_date: Annotated[str, Form()],
    due_time: Annotated[str | None, Form()] = None,
    teacher_name: Annotated[str | None, Form()] = None,
    location: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
):
    try:
        fields = _read_form(subject_name, due_date, due_time, teacher_name, location, notes)
    except FormError as error:
        return render(
            request,
            "assignments/form.html",
            {"user": user, "assignment": None, "error": str(error)},
        )
    await assignments_service.create(session, user.id, **fields)
    return RedirectResponse("/assignments", status_code=303)


@router.get("/{assignment_id}/edit")
async def edit_form(
    request: Request, user: CurrentUser, session: DbSession, assignment_id: int
):
    assignment = await _owned_assignment(session, user, assignment_id)
    return render(
        request, "assignments/form.html", {"user": user, "assignment": assignment}
    )


@router.post("/{assignment_id}/edit", dependencies=[Depends(verify_csrf)])
async def edit(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    assignment_id: int,
    subject_name: Annotated[str, Form()],
    due_date: Annotated[str, Form()],
    due_time: Annotated[str | None, Form()] = None,
    teacher_name: Annotated[str | None, Form()] = None,
    location: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
):
    assignment = await _owned_assignment(session, user, assignment_id)
    try:
        fields = _read_form(subject_name, due_date, due_time, teacher_name, location, notes)
    except FormError as error:
        return render(
            request,
            "assignments/form.html",
            {"user": user, "assignment": assignment, "error": str(error)},
        )
    await assignments_service.update(session, assignment, fields)
    return RedirectResponse("/assignments", status_code=303)


@router.post("/{assignment_id}/status", dependencies=[Depends(verify_csrf)])
async def set_status(
    user: CurrentUser,
    session: DbSession,
    assignment_id: int,
    done: Annotated[str, Form()],
):
    assignment = await _owned_assignment(session, user, assignment_id)
    await assignments_service.set_done(session, assignment, done == "1")
    return RedirectResponse("/assignments", status_code=303)


@router.post("/{assignment_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete(user: CurrentUser, session: DbSession, assignment_id: int):
    assignment = await _owned_assignment(session, user, assignment_id)
    await assignments_service.delete(session, assignment)
    return RedirectResponse("/assignments", status_code=303)
