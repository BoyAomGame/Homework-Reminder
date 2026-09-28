"""Registration, login, and logout."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.deps import SESSION_USER_KEY, DbSession, verify_csrf
from app.auth.security import hash_password, verify_password
from app.config import get_settings
from app.db.models import User
from app.db.session import SessionFactory
from app.web.templating import render

router = APIRouter()

MIN_PASSWORD_LENGTH = 8


@router.get("/register")
async def register_form(request: Request):
    async with SessionFactory() as session:
        if await session.scalar(select(User.id).limit(1)) is not None:
            return RedirectResponse("/login", status_code=303)
    return render(request, "auth/register.html")


@router.post("/register", dependencies=[Depends(verify_csrf)])
async def register(
    request: Request,
    session: DbSession,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    email = email.strip().lower()
    error = None
    if await session.scalar(select(User.id).limit(1)) is not None:
        return RedirectResponse("/login", status_code=303)
    if "@" not in email or len(email) > 255:
        error = "กรุณากรอกอีเมลให้ถูกต้อง"
    elif len(password) < MIN_PASSWORD_LENGTH:
        error = f"รหัสผ่านต้องมีอย่างน้อย {MIN_PASSWORD_LENGTH} ตัวอักษร"
    if error:
        return render(request, "auth/register.html", {"error": error, "email": email})

    user = User(
        email=email,
        password_hash=hash_password(password),
        reminder_offsets=list(get_settings().default_reminder_offsets),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return RedirectResponse("/login", status_code=303)

    request.session[SESSION_USER_KEY] = user.id
    return RedirectResponse("/", status_code=303)


@router.get("/login")
async def login_form(request: Request):
    return render(request, "auth/login.html")


@router.post("/login", dependencies=[Depends(verify_csrf)])
async def login(
    request: Request,
    session: DbSession,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    email = email.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(user.password_hash, password):
        return render(
            request, "auth/login.html",
            {"error": "อีเมลหรือรหัสผ่านไม่ถูกต้อง", "email": email},
        )
    request.session[SESSION_USER_KEY] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
