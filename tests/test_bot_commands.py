from datetime import date, timedelta

from app import bot_messages
from app.bot_commands import handle_dm
from app.db.session import SessionFactory
from app.llm.base import DetectedIntent, LLMError, ParsedAssignment
from app.services import assignments as asvc

from tests.fakes import FakeTextLLM


def _tomorrow() -> date:
    return date.today() + timedelta(days=1)


def _intent(**kwargs) -> FakeTextLLM:
    """A fake LLM whose router always returns this one DetectedIntent."""
    return FakeTextLLM(router=DetectedIntent(**kwargs))


async def _add(user, subject="ฟิสิกส์") -> int:
    async with SessionFactory() as session:
        assignment = await asvc.create(
            session, user.id, subject_name=subject, due_date=_tomorrow()
        )
        return assignment.id


async def test_help_and_empty_message(user):
    assert await handle_dm(user.id, "ช่วยเหลือ", _intent(command="help")) == bot_messages.HELP
    # An empty message short-circuits to HELP before the LLM is ever consulted.
    assert await handle_dm(user.id, "   ") == bot_messages.HELP


async def test_natural_list_and_done_flow(user):
    aid = await _add(user)
    listing = await handle_dm(user.id, "เหลืออะไรบ้าง", _intent(command="list"))
    assert f"#{aid}" in listing and "ฟิสิกส์" in listing

    reply = await handle_dm(
        user.id, "ทำฟิสิกส์เสร็จแล้ว", _intent(command="done", assignment_id=aid)
    )
    assert "เสร็จแล้ว" in reply
    assert "ไม่มีการบ้านค้าง" in await handle_dm(user.id, "list", _intent(command="list"))


async def test_delete_asks_before_deleting_then_confirms(user):
    aid = await _add(user)
    reply = await handle_dm(
        user.id, "ลบฟิสิกส์", _intent(command="delete", assignment_id=aid)
    )
    assert "ยืนยัน" in reply and f"#{aid}" in reply
    # The yes/no answer is handled without consulting the LLM.
    done = await handle_dm(user.id, "ใช่", _intent(command="unknown"))
    assert "ลบ" in done
    assert "ไม่มีการบ้านค้าง" in await handle_dm(user.id, "list", _intent(command="list"))


async def test_delete_can_be_cancelled(user):
    aid = await _add(user)
    await handle_dm(user.id, "ลบฟิสิกส์", _intent(command="delete", assignment_id=aid))
    assert await handle_dm(user.id, "ไม่", _intent(command="unknown")) == bot_messages.DELETE_CANCELLED
    # Still there.
    listing = await handle_dm(user.id, "list", _intent(command="list"))
    assert f"#{aid}" in listing


async def test_unresolved_target_asks_which(user):
    await _add(user)
    assert await handle_dm(user.id, "เสร็จแล้ว", _intent(command="done")) == bot_messages.CLARIFY_WHICH
    assert await handle_dm(user.id, "ลบอันนั้น", _intent(command="delete")) == bot_messages.CLARIFY_WHICH


async def test_hallucinated_id_is_rejected(user):
    reply = await handle_dm(user.id, "ทำเสร็จแล้ว", _intent(command="done", assignment_id=999999))
    assert "ไม่พบ" in reply


async def test_natural_edit(user):
    aid = await _add(user)
    reply = await handle_dm(
        user.id, "เปลี่ยนเวลาเป็น 07:15",
        _intent(command="edit", assignment_id=aid, edit_field="time", edit_value="07:15"),
    )
    assert "07:15" in reply

    reply = await handle_dm(
        user.id, "เปลี่ยนวันส่ง",
        _intent(command="edit", assignment_id=aid, edit_field="date", edit_value="ไม่ใช่วันที่"),
    )
    assert reply == bot_messages.EDIT_BAD_DATE

    reply = await handle_dm(
        user.id, "แก้",
        _intent(command="edit", assignment_id=aid, edit_field="subject", edit_value=None),
    )
    assert reply == bot_messages.EDIT_USAGE


async def test_cannot_touch_another_users_assignment(user):
    aid = await _add(user)
    async with SessionFactory() as session:
        from tests.conftest import _PASSWORD_HASH
        from app.db.models import User
        intruder = User(email="intruder@test.local", password_hash=_PASSWORD_HASH, owner_slot=2,
                        reminder_offsets=[0])
        session.add(intruder)
        await session.commit()
    assert "ไม่พบ" in await handle_dm(intruder.id, "เสร็จ", _intent(command="done", assignment_id=aid))
    assert "ไม่พบ" in await handle_dm(intruder.id, "ลบ", _intent(command="delete", assignment_id=aid))


async def test_natural_language_add_confirms_in_thai(user):
    llm = _intent(command="add", assignment=ParsedAssignment(
        subject_name="เคมี", location="ใบงาน 3", due_date=_tomorrow(),
    ))
    reply = await handle_dm(user.id, "ใบงานเคมี ส่งพรุ่งนี้", llm)
    assert reply.startswith("รับทราบ")
    assert "เคมี" in reply and "ใบงาน 3" in reply
    assert "จะเตือน 1 วัน และ 3 ชม." in reply


async def test_unknown_intent_asks_to_rephrase(user):
    assert await handle_dm(user.id, "สวัสดีจ้า", _intent(command="unknown")) == bot_messages.CLARIFY_UNPARSEABLE


async def test_llm_failure_falls_back_to_clarify(user):
    llm = FakeTextLLM(router=LLMError("model down"))
    assert await handle_dm(user.id, "อะไรสักอย่าง", llm) == bot_messages.CLARIFY_UNPARSEABLE
