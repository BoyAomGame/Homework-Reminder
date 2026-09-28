"""Local defaults and secrets persisted beside the SQLite database."""

import os
import secrets
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


def _persistent_secret(name: str, create) -> str:
    data_dir = Path(os.getenv("HOMEWORK_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / name
    if not path.exists():
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(create())
        except FileExistsError:
            pass
    return path.read_text(encoding="utf-8").strip()


class Settings(BaseSettings):
    # Environment overrides exist for isolated tests; normal installation needs no .env.
    model_config = SettingsConfigDict(extra="ignore")
    database_url: str = "sqlite+aiosqlite:///data/homework.db"
    token_encryption_key: str = ""
    session_secret: str = ""
    default_reminder_offsets: list[int] = [1440, 180, 0]
    overdue_check_interval_minutes: int = 30


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.token_encryption_key:
        settings.token_encryption_key = _persistent_secret(
            "bot-token.key", lambda: Fernet.generate_key().decode()
        )
    if not settings.session_secret:
        settings.session_secret = _persistent_secret(
            "session.key", lambda: secrets.token_urlsafe(48)
        )
    return settings
