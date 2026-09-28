"""FastAPI dependencies for authentication and CSRF protection."""

from typing import Annotated

from fastapi import Depends, Form, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import check_csrf_token
from app.db.models import User
from app.db.session import get_session

SESSION_USER_KEY = "user_id"


class LoginRequired(Exception):
    """Raised when an anonymous request hits a protected page.

    Handled in main.py by redirecting to the login form.
    """


async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    user_id = request.session.get(SESSION_USER_KEY)
    if user_id is None:
        raise LoginRequired()
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:  # account deleted while the cookie was still alive
        request.session.clear()
        raise LoginRequired()
    return user


async def verify_csrf(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
) -> None:
    """Dependency for every state-changing (POST) form route."""
    if not check_csrf_token(request.session, csrf_token):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="รหัสป้องกันคำขอไม่ถูกต้องหรือไม่มี"
        )


CurrentUser = Annotated[User, Depends(current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
