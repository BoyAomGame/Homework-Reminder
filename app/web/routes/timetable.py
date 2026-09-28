"""Weekly timetable editor and the photo-import flow.

Import is deliberately two-step: the vision model's transcription is
shown in an editable table and nothing is saved until the user
confirms — an image parse is never trusted blindly.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from app.auth.deps import CurrentUser, DbSession, verify_csrf
from app.llm.base import LLMError
from app.providers.registry import get_vision_provider
from app.services import timetable as timetable_service
from app.web.forms import FormError, clean_text, parse_int_in_range, parse_optional_time
from app.web.templating import render

router = APIRouter(prefix="/timetable")

MAX_IMAGE_BYTES = 8 * 1024 * 1024


async def _render_page(
    request: Request, user, session, *, error: str | None = None
):
    entries = await timetable_service.list_for_user(session, user.id)
    return render(
        request,
        "timetable/list.html",
        {
            "user": user,
            "entries": entries,
            "effective_start_time": timetable_service.effective_start_time,
            "error": error,
        },
    )


@router.get("")
async def list_timetable(request: Request, user: CurrentUser, session: DbSession):
    return await _render_page(request, user, session)


@router.post("", dependencies=[Depends(verify_csrf)])
async def upsert_entry(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    day_of_week: Annotated[str, Form()],
    period_number: Annotated[str, Form()],
    subject_name: Annotated[str, Form()],
    teacher_name: Annotated[str | None, Form()] = None,
    room: Annotated[str | None, Form()] = None,
    start_time: Annotated[str | None, Form()] = None,
    end_time: Annotated[str | None, Form()] = None,
):
    try:
        subject = clean_text(subject_name, max_length=200)
        if not subject:
            raise FormError("กรุณากรอกชื่อวิชา")
        await timetable_service.upsert(
            session,
            user.id,
            day_of_week=parse_int_in_range(day_of_week, 0, 6, field="วัน"),
            period_number=parse_int_in_range(period_number, 1, 20, field="คาบ"),
            subject_name=subject,
            teacher_name=clean_text(teacher_name, max_length=200),
            room=clean_text(room, max_length=200),
            start_time=parse_optional_time(start_time, field="เวลาเริ่ม"),
            end_time=parse_optional_time(end_time, field="เวลาจบ"),
        )
    except FormError as error:
        return await _render_page(request, user, session, error=str(error))
    return RedirectResponse("/timetable", status_code=303)


@router.get("/import")
async def import_form(request: Request, user: CurrentUser):
    return render(request, "timetable/import.html", {"user": user})


@router.post("/import", dependencies=[Depends(verify_csrf)])
async def import_image(request: Request, user: CurrentUser, image: UploadFile):
    if not (image.content_type or "").startswith("image/"):
        return render(
            request, "timetable/import.html",
            {"user": user, "error": "กรุณาอัปโหลดไฟล์รูปตารางเรียน"},
        )
    data = await image.read()
    if len(data) > MAX_IMAGE_BYTES:
        return render(
            request, "timetable/import.html",
            {"user": user, "error": "รูปใหญ่เกิน 8 MB"},
        )
    try:
        rows = await get_vision_provider().parse_timetable_image(
            data, image.content_type
        )
    except LLMError:
        return render(
            request, "timetable/import.html",
            {"user": user,
             "error": "อ่านตารางเรียนจากรูปไม่สำเร็จ ตรวจการตั้งค่า AI หรือลองใช้รูปที่คมชัดขึ้น"},
        )
    return render(
        request, "timetable/import_confirm.html", {"user": user, "rows": rows}
    )


@router.post("/import/confirm", dependencies=[Depends(verify_csrf)])
async def import_confirm(request: Request, user: CurrentUser, session: DbSession):
    """Save the user-corrected rows, replacing the whole timetable."""
    form = await request.form()
    fields = ["day_of_week", "period_number", "subject_name",
              "teacher_name", "room", "start_time", "end_time"]
    columns = {name: form.getlist(name) for name in fields}

    rows, seen_slots = [], set()
    try:
        for i in range(len(columns["subject_name"])):
            subject = clean_text(columns["subject_name"][i], max_length=200)
            if not subject:
                continue  # clearing the subject removes the row
            slot = (
                parse_int_in_range(columns["day_of_week"][i], 0, 6, field="วัน"),
                parse_int_in_range(columns["period_number"][i], 1, 20, field="คาบ"),
            )
            if slot in seen_slots:
                raise FormError(
                    f"คาบซ้ำ: วันที่ {slot[0] + 1} คาบที่ {slot[1]}"
                )
            seen_slots.add(slot)
            rows.append({
                "day_of_week": slot[0],
                "period_number": slot[1],
                "subject_name": subject,
                "teacher_name": clean_text(columns["teacher_name"][i], max_length=200),
                "room": clean_text(columns["room"][i], max_length=200),
                "start_time": parse_optional_time(columns["start_time"][i]),
                "end_time": parse_optional_time(columns["end_time"][i]),
            })
    except (FormError, IndexError) as error:
        return render(
            request, "timetable/import.html",
            {"user": user, "error": f"บันทึกไม่สำเร็จ: {error} กรุณานำเข้าใหม่"},
        )

    await timetable_service.replace_all(session, user.id, rows)
    return RedirectResponse("/timetable", status_code=303)


@router.post("/{entry_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_entry(user: CurrentUser, session: DbSession, entry_id: int):
    entry = await timetable_service.get_for_user(session, user.id, entry_id)
    if entry is None:
        raise HTTPException(404, "ไม่พบคาบเรียน")
    await timetable_service.delete(session, entry)
    return RedirectResponse("/timetable", status_code=303)
