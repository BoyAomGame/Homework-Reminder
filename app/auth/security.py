"""Password hashing, CSRF tokens, and bot-token encryption."""

import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_hasher = PasswordHasher()


# --- Passwords (argon2id) --------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


# --- CSRF -------------------------------------------------------------------

CSRF_SESSION_KEY = "csrf_token"


def issue_csrf_token(session: dict) -> str:
    """Return the session's CSRF token, creating one if needed."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def check_csrf_token(session: dict, submitted: str | None) -> bool:
    expected = session.get(CSRF_SESSION_KEY)
    return bool(expected and submitted) and hmac.compare_digest(expected, submitted)


# --- Discord bot token encryption (Fernet) ----------------------------------

def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("ไม่พบกุญแจเข้ารหัสในพื้นที่เก็บข้อมูล")
    return Fernet(key.encode())


def encrypt_bot_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode() if value else ""


def decrypt_bot_token(token_encrypted: str) -> str:
    try:
        return _fernet().decrypt(token_encrypted.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "ถอดรหัสโทเคนบอตไม่ได้ กรุณาตรวจสอบไฟล์กุญแจในพื้นที่เก็บข้อมูล"
        ) from exc
