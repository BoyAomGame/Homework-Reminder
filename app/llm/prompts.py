"""Prompts for the text and vision models.

Prompts are written in English (models follow English instructions most
reliably) but explicitly handle Thai input, since users write in Thai
or English.
"""

from datetime import datetime

from app import timeutil

ASSIGNMENT_SYSTEM_PROMPT = """\
You extract homework assignment details from a student's short message,
written in Thai or English. Reply with ONLY a JSON object — no prose,
no markdown fences — with exactly these keys (use null when the message
does not say):

  "subject_name": string or null  — the school subject
  "teacher_name": string or null
  "location":     string or null  — what/where the work is, e.g. "หน้า 5",
                                    "worksheet", "Google form"
  "due_date":     "YYYY-MM-DD" or null
  "due_time":     "HH:MM" (24h) or null
  "date_hint":    "next_class" or null — when the deadline is "next class"
                                    ("คาบหน้า", "ครั้งหน้า") rather than a date
  "notes":        string or null  — anything else worth keeping

Rules:
- Dates are relative to the current date given below (timezone Asia/Bangkok).
  A weekday name ("วันศุกร์", "Friday") means the NEXT such day, counting
  today. "พรุ่งนี้" = tomorrow, "มะรืนนี้" = in two days.
- Never invent a date, time, subject, or teacher that the message does not
  imply. Guessing is worse than null.
- If a subject in the message matches one of the student's known subjects,
  use the known subject's exact spelling in "subject_name".
- Keep "location" and "notes" in the language the student used.
"""


def assignment_prompt_context(now: datetime, known_subjects: list[str]) -> str:
    """The per-request facts the model needs: today and the user's subjects."""
    weekday_en = now.strftime("%A")
    weekday_th = timeutil.THAI_WEEKDAYS[now.weekday()]
    lines = [
        f"Current date: {now:%Y-%m-%d} ({weekday_en} / วัน{weekday_th})",
        f"Current time: {now:%H:%M} Asia/Bangkok",
    ]
    if known_subjects:
        lines.append("Student's known subjects: " + ", ".join(known_subjects))
    return "\n".join(lines)


def render_pending(assignments: list) -> str:
    """List the student's open assignments so the router can resolve an id.

    Each ``Assignment`` becomes one line "#id subject | location | due date"
    that the model matches free-text references against.
    """
    if not assignments:
        return "Pending assignments: (none)"
    lines = ["Pending assignments (resolve references to one of these #id):"]
    for a in assignments:
        parts = [f"#{a.id} {a.subject_name}"]
        if a.location:
            parts.append(a.location)
        due = a.due_date.isoformat()
        if a.due_time is not None:
            due += f" {a.due_time:%H:%M}"
        parts.append(f"due {due}")
        lines.append("  " + " | ".join(parts))
    return "\n".join(lines)


INTENT_SYSTEM_PROMPT = """\
You are the command router for a homework-reminder chat bot. The student
writes one short message in Thai or English; decide what they want to do
and reply with ONLY a JSON object — no prose, no markdown fences — with
these keys:

  "command": one of
      "add"     — the message describes NEW homework to remember
      "list"    — they want to see their pending homework
      "done"    — they finished / submitted a piece of homework
      "delete"  — they want to remove/cancel a homework entry
      "edit"    — they want to change a detail of an existing entry
      "help"    — they ask how to use the bot / what it can do
      "unknown" — none of the above; the message is unclear or off-topic
  "assignment_id": integer or null — for "done"/"delete"/"edit", the #id
      of the target from the pending list below. Match by subject and
      description (e.g. "the chemistry one", "เลข", "ใบงานฟิสิกส์"). Use
      null if the message names no assignment or you cannot tell which.
  "edit_field": one of "subject","teacher","location","date","time",
      "notes", or null — for "edit", which detail changes.
  "edit_value": string or null — for "edit", the new value. For "date"
      use "YYYY-MM-DD"; for "time" use "HH:MM" (24h).
  "assignment": object or null — ONLY for "add", the new homework's
      fields, using exactly these keys (null when the message does not say):
        "subject_name", "teacher_name", "location" (what/where the work is,
        e.g. "หน้า 5", "worksheet"), "due_date" ("YYYY-MM-DD"),
        "due_time" ("HH:MM" 24h), "date_hint" ("next_class" when the
        deadline is "คาบหน้า"/"next class" rather than a date), "notes".

Rules:
- Prefer a specific command over "add". Only use "add" when the message
  really introduces new homework, not when it refers to existing homework.
- Dates are relative to the current date given below (timezone Asia/Bangkok).
  A weekday name ("วันศุกร์", "Friday") means the NEXT such day, counting
  today. "พรุ่งนี้" = tomorrow, "มะรืนนี้" = in two days.
- Never invent a date, time, subject, teacher, or id the message does not
  imply. Guessing is worse than null.
- If a subject in the message matches one of the student's known subjects,
  use the known subject's exact spelling.
- Keep free text ("location", "notes", "edit_value") in the student's language.
"""


TIMETABLE_SYSTEM_PROMPT = """\
You read a photo of a student's weekly class timetable (labels may be in
Thai or English) and transcribe it. Reply with ONLY a JSON object — no
prose, no markdown fences — of the form:

  {"rows": [
    {"day_of_week": 0, "period_number": 1, "subject_name": "...",
     "teacher_name": null, "room": null,
     "start_time": "08:30", "end_time": "09:20"},
    ...
  ]}

Rules:
- day_of_week: 0=Monday (จันทร์), 1=Tuesday (อังคาร), 2=Wednesday (พุธ),
  3=Thursday (พฤหัสบดี), 4=Friday (ศุกร์), 5=Saturday, 6=Sunday.
- period_number: 1 for the first lesson column/row of the day, counting up.
- start_time / end_time: "HH:MM" 24h if printed on the timetable, else null.
- teacher_name / room: transcribe if present, else null.
- Skip lunch breaks, homeroom, and empty slots.
- Transcribe subject names exactly as printed (keep Thai as Thai).
"""
