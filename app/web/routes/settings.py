"""Account settings: Discord bot token, account linking, reminder offsets."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from urllib.parse import urlsplit

from app import runtime
from app.auth.deps import CurrentUser, DbSession, verify_csrf
from app.services import bots as bots_service
from app.auth.security import encrypt_secret
from app.db.models import AppSettings
from app.providers.registry import reload_configuration
from app.web.forms import FormError
from app.web.templating import render

router = APIRouter(prefix="/settings")

MAX_OFFSET_MINUTES = 60 * 24 * 30  # a month before the deadline is plenty


async def _render_page(request: Request, user, session, *, error: str | None = None):
    bot = await bots_service.get_for_user(session, user.id)
    ai = await session.get(AppSettings, 1)
    return render(
        request,
        "settings.html",
        {"user": user, "bot": bot, "ai": ai, "error": error},
    )


@router.get("")
async def settings_page(request: Request, user: CurrentUser, session: DbSession):
    return await _render_page(request, user, session)


@router.post("/bot-token", dependencies=[Depends(verify_csrf)])
async def set_bot_token(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    bot_token: Annotated[str, Form()],
):
    token = bot_token.strip()
    if len(token) < 50 or " " in token:
        return await _render_page(
            request, user, session,
            error="โทเคนบอต Discord ดูไม่ถูกต้อง",
        )
    await bots_service.set_token(session, user.id, token)
    return RedirectResponse("/settings", status_code=303)


@router.post("/bot-token/delete", dependencies=[Depends(verify_csrf)])
async def delete_bot_token(user: CurrentUser, session: DbSession):
    bot = await bots_service.get_for_user(session, user.id)
    if bot is None:
        raise HTTPException(404, "ยังไม่ได้ตั้งค่าบอต")
    await bots_service.remove(session, bot)
    return RedirectResponse("/settings", status_code=303)


@router.post("/bot/enabled", dependencies=[Depends(verify_csrf)])
async def set_bot_enabled(
    user: CurrentUser, session: DbSession, enabled: Annotated[str, Form()]
):
    bot = await bots_service.get_for_user(session, user.id)
    if bot is None:
        raise HTTPException(404, "ยังไม่ได้ตั้งค่าบอต")
    await bots_service.set_enabled(session, bot, enabled == "1")
    return RedirectResponse("/settings", status_code=303)


@router.post("/link-code", dependencies=[Depends(verify_csrf)])
async def regenerate_link_code(user: CurrentUser, session: DbSession):
    bot = await bots_service.get_for_user(session, user.id)
    if bot is None:
        raise HTTPException(404, "ยังไม่ได้ตั้งค่าบอต")
    await bots_service.regenerate_link_code(session, bot)
    return RedirectResponse("/settings", status_code=303)


@router.post("/reminders", dependencies=[Depends(verify_csrf)])
async def set_reminder_offsets(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    offsets: Annotated[str, Form()],
):
    try:
        parsed = sorted(
            {int(part) for part in offsets.replace(" ", "").split(",") if part},
            reverse=True,
        )
        if not parsed:
            raise ValueError
        if any(o < 0 or o > MAX_OFFSET_MINUTES for o in parsed):
            raise FormError("ระยะเวลาเตือนต้องอยู่ระหว่าง 0 ถึง 43200 นาที")
    except ValueError:
        return await _render_page(
            request, user, session,
            error="กรอกจำนวนนาทีคั่นด้วยจุลภาค เช่น 1440, 180, 0",
        )
    except FormError as error:
        return await _render_page(request, user, session, error=str(error))

    user.reminder_offsets = parsed
    await session.commit()
    scheduler = runtime.scheduler()
    if scheduler is not None:  # rebuild this user's reminder jobs with new offsets
        scheduler.reschedule_user(user.id)
    return RedirectResponse("/settings", status_code=303)


@router.post("/ai", dependencies=[Depends(verify_csrf)])
async def save_ai_settings(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    text_api_key: Annotated[str, Form()] = "",
    text_base_url: Annotated[str, Form()] = "",
    text_model: Annotated[str, Form()] = "",
    vision_api_key: Annotated[str, Form()] = "",
    vision_base_url: Annotated[str, Form()] = "",
    vision_model: Annotated[str, Form()] = "",
):
    text_base_url = text_base_url.strip().rstrip("/")
    vision_base_url = vision_base_url.strip().rstrip("/")
    text_model = text_model.strip()
    vision_model = vision_model.strip()
    for url in (text_base_url, vision_base_url):
        if url and (urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc or len(url) > 500):
            return await _render_page(request, user, session, error="URL ของบริการ AI ต้องขึ้นต้นด้วย http:// หรือ https://")
    if not text_base_url or not text_model or len(text_model) > 200 or len(vision_model) > 200:
        return await _render_page(request, user, session, error="กรุณากรอก URL และชื่อโมเดลสำหรับอ่านข้อความ")
    row = await session.get(AppSettings, 1)
    if not text_api_key.strip() and not (row and row.text_api_key_encrypted):
        return await _render_page(request, user, session, error="กรุณากรอก API Key สำหรับอ่านข้อความ")
    if row is None:
        row = AppSettings(id=1)
        session.add(row)
    row.text_base_url = text_base_url
    row.text_model = text_model
    row.vision_base_url = vision_base_url
    row.vision_model = vision_model
    if text_api_key.strip():
        row.text_api_key_encrypted = encrypt_secret(text_api_key.strip())
    if vision_api_key.strip():
        row.vision_api_key_encrypted = encrypt_secret(vision_api_key.strip())
    await session.commit()
    await reload_configuration(session)
    return RedirectResponse("/settings", status_code=303)
