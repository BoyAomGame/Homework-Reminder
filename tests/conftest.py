"""Test configuration.

Tests run against a throwaway SQLite database and stub credentials, so
they need neither PostgreSQL nor any external API. The environment
must be prepared before any ``app`` module is imported, because the
engine binds to DATABASE_URL at import time.
"""

import os
import tempfile
import uuid

from cryptography.fernet import Fernet

_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="hwremind-test-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"
os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["DEEPSEEK_API_KEY"] = "test-key-never-used"

import pytest_asyncio  # noqa: E402

from app.auth.security import hash_password  # noqa: E402
from app.db.models import Base, User  # noqa: E402
from app.db.session import SessionFactory, engine  # noqa: E402

_PASSWORD_HASH = hash_password("password123")  # argon2 is slow; hash once


@pytest_asyncio.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def user() -> User:
    """A fresh user with the default reminder offsets."""
    async with SessionFactory() as session:
        new_user = User(
            email=f"{uuid.uuid4().hex[:12]}@test.local",
            password_hash=_PASSWORD_HASH,
            reminder_offsets=[1440, 180, 0],
        )
        session.add(new_user)
        await session.commit()
        return new_user
