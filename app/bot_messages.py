"""Every Thai string the Discord bot sends, in one place.

Users may write in Thai or English, but the bot always answers in Thai.
Keeping the strings together makes the bot's voice easy to review and
keeps Thai out of the program logic.
"""

from app import timeutil
from app.db.models import Assignment

# --- Linking ---------------------------------------------------------------

LINK_PROMPT = (
    "สวัสดี! บอทนี้ยังไม่ได้ผูกกับบัญชีผู้ใช้\n"
    "กรุณาส่งรหัสเชื่อมต่อ 6 หลักจากหน้าตั้งค่าของเว็บมาที่นี่"
)
LINK_SUCCESS = (
    "เชื่อมต่อสำเร็จ! 🎉\n"
    "พิมพ์บอกการบ้านได้เลย เช่น «การบ้านเลข หน้า 5 ส่งวันศุกร์»\n"
    "หรือพิมพ์ «ช่วยเหลือ» เพื่อดูคำสั่งทั้งหมด"
)
LINK_WRONG_CODE = "รหัสไม่ถูกต้อง ลองตรวจสอบรหัสในหน้าตั้งค่าของเว็บอีกครั้ง"

# --- Clarifications (we never guess) ----------------------------------------

CLARIFY_UNPARSEABLE = (
    "ขออภัย อ่านข้อความนี้ไม่เข้าใจ 🙏\n"
    "ลองพิมพ์ใหม่ เช่น «การบ้านเลข หน้า 5 ส่งวันศุกร์»"
)
CLARIFY_SUBJECT = "ยังไม่ทราบวิชา ช่วยบอกวิชาด้วย เช่น «การบ้านเลข หน้า 5 ส่งวันศุกร์»"
CLARIFY_DUE_DATE = (
    "ยังไม่ทราบกำหนดส่ง 📅 ช่วยบอกวันส่งด้วย เช่น «ส่งวันศุกร์» หรือ «ส่ง 20 มิ.ย.»"
)

# --- Errors ------------------------------------------------------------------

ERROR_GENERIC = "ขออภัย เกิดข้อผิดพลาด ลองใหม่อีกครั้งภายหลัง"
NOT_FOUND = "ไม่พบการบ้านหมายเลขนั้น พิมพ์ «รายการ» เพื่อดูหมายเลขทั้งหมด"
CLARIFY_WHICH = "ไม่แน่ใจว่าหมายถึงการบ้านไหน พิมพ์ «รายการ» เพื่อดูหมายเลขทั้งหมด แล้วบอกใหม่อีกครั้ง"

# --- Delete confirmation ----------------------------------------------------

DELETE_CANCELLED = "ยกเลิกการลบแล้ว ไม่มีอะไรถูกลบ"


def confirm_delete(assignment: Assignment) -> str:
    return (
        f"ต้องการลบ {_line(assignment)} ใช่ไหม?\n"
        "ตอบ «ใช่» เพื่อยืนยัน หรือ «ไม่» เพื่อยกเลิก"
    )


HELP = (
    "พิมพ์บอกได้เลยด้วยภาษาธรรมดา บอทจะเข้าใจเองว่าต้องการอะไร เช่น:\n"
    "• เพิ่มการบ้าน — «การบ้านเลข หน้า 5 ส่งวันศุกร์»\n"
    "• ดูที่ค้างอยู่ — «เหลืออะไรบ้าง»\n"
    "• ทำเสร็จแล้ว — «ทำการบ้านเลขเสร็จแล้ว»\n"
    "• แก้ไข — «เปลี่ยนเวลาส่งการบ้านเลขเป็น 08:30»\n"
    "• ลบ — «ลบการบ้านเคมี» (จะถามยืนยันก่อน)\n"
    "• «ช่วยเหลือ» — ดูข้อความนี้"
)


def _line(assignment: Assignment, *, overdue: bool = False) -> str:
    parts = [f"#{assignment.id} {assignment.subject_name}"]
    if assignment.location:
        parts.append(assignment.location)
    parts.append("ส่ง" + timeutil.thai_datetime(assignment.due_date, assignment.due_time))
    if overdue:
        parts.append("⚠️ เลยกำหนดแล้ว")
    return " ".join(parts)


def confirmation(
    assignment: Assignment,
    offsets: list[int],
    *,
    time_from_timetable: bool = False,
    date_moved_to_meeting: bool = False,
) -> str:
    """Thai confirmation after a successful parse, so mistakes get caught."""
    parts = [assignment.subject_name]
    if assignment.location:
        parts.append(assignment.location)
    if assignment.teacher_name:
        name = assignment.teacher_name
        parts.append(name if name.startswith("ครู") else f"ครู{name}")
    when = timeutil.thai_datetime(assignment.due_date, assignment.due_time)

    text = f"รับทราบ — {' '.join(parts)} กำหนดส่ง{when}"
    if offsets:
        described = " และ ".join(timeutil.thai_offset(o) for o in sorted(offsets, reverse=True) if o > 0)
        if described:
            text += f"\nจะเตือน {described} ก่อนถึงกำหนด"
    if date_moved_to_meeting:
        text += "\n(เลื่อนไปตามคาบเรียนถัดไปของวิชานี้ในตารางเรียน)"
    elif assignment.due_time is None:
        text += "\n(ไม่พบเวลาเรียนในตาราง จะถือว่าส่งภายในสิ้นวันนั้น)"
    elif time_from_timetable:
        text += "\n(เวลามาจากตารางเรียน)"
    text += f"\nหมายเลขการบ้าน: #{assignment.id} — พิมพ์ «เสร็จ {assignment.id}» เมื่อทำเสร็จ"
    return text


def pending_list(assignments: list[Assignment], is_overdue) -> str:
    if not assignments:
        return "ไม่มีการบ้านค้างอยู่ เยี่ยมมาก! 🎉"
    lines = ["การบ้านที่ค้างอยู่:"]
    lines += [f"• {_line(a, overdue=is_overdue(a))}" for a in assignments]
    return "\n".join(lines)


def marked_done(assignment: Assignment) -> str:
    return f"เยี่ยม! ✅ บันทึกว่า #{assignment.id} {assignment.subject_name} เสร็จแล้ว"


def deleted(assignment: Assignment) -> str:
    return f"ลบ #{assignment.id} {assignment.subject_name} แล้ว"


def edited(assignment: Assignment) -> str:
    return "แก้ไขแล้ว ✏️\n" + _line(assignment)


EDIT_USAGE = (
    "รูปแบบคำสั่งแก้ไข: «แก้ <หมายเลข> <สิ่งที่แก้> <ค่าใหม่>»\n"
    "สิ่งที่แก้ได้: วิชา / ครู / งาน / วันส่ง (ปี-เดือน-วัน) / เวลา (ชช:นน) / โน้ต\n"
    "เช่น «แก้ 3 วันส่ง 2026-06-20» หรือ «แก้ 3 เวลา 08:30»"
)
EDIT_BAD_DATE = "วันที่ไม่ถูกต้อง ใช้รูปแบบ ปี-เดือน-วัน เช่น 2026-06-20"
EDIT_BAD_TIME = "เวลาไม่ถูกต้อง ใช้รูปแบบ ชช:นน เช่น 08:30"


def reminder(assignment: Assignment, minutes_before: int) -> str:
    when = timeutil.thai_datetime(assignment.due_date, assignment.due_time)
    if minutes_before <= 0:
        head = "⏰ ถึงกำหนดส่งแล้ว!"
    else:
        head = f"⏰ อีก {timeutil.thai_offset(minutes_before)} จะถึงกำหนดส่ง"
    return f"{head}\n{_line(assignment)}\n(กำหนดส่ง{when} — พิมพ์ «เสร็จ {assignment.id}» เมื่อส่งแล้ว)"


def overdue_warning(assignment: Assignment) -> str:
    return (
        "⚠️ เลยกำหนดส่งแล้ว ยังไม่ได้ทำ!\n"
        f"{_line(assignment)}\n"
        f"(พิมพ์ «เสร็จ {assignment.id}» ถ้าส่งแล้ว หรือ «แก้ {assignment.id} วันส่ง ...» เพื่อเลื่อนกำหนด)"
    )
