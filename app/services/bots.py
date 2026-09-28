"""Manage a user's Discord bot record (token, link state, enablement).

Starting/stopping the actual gateway connection is the bot manager's
job; these functions keep the database row correct and nudge the
manager (when it is running) to apply the change.
"""

import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import runtime
from app.auth.security import encrypt_bot_token
from app.db.models import BotStatus, DiscordBot

_LINK_CODE_ALPHABET = string.ascii_uppercase + string.digits
LINK_CODE_LENGTH = 6


def generate_link_code() -> str:
    return "".join(secrets.choice(_LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))


async def get_for_user(session: AsyncSession, user_id: int) -> DiscordBot | None:
    return await session.scalar(
        select(DiscordBot).where(DiscordBot.user_id == user_id)
    )


async def set_token(session: AsyncSession, user_id: int, token: str) -> DiscordBot:
    """Store a new (or replacement) bot token, encrypted, and reset link state."""
    bot = await get_for_user(session, user_id)
    if bot is None:
        bot = DiscordBot(user_id=user_id)
        session.add(bot)
    bot.token_encrypted = encrypt_bot_token(token.strip())
    bot.enabled = True
    bot.status = BotStatus.STOPPED
    bot.last_error = None
    # A new token means a new bot: the Discord account must link again.
    bot.discord_user_id = None
    bot.link_code = generate_link_code()
    await session.commit()
    await _apply_to_manager(bot)
    return bot


async def remove(session: AsyncSession, bot: DiscordBot) -> None:
    manager = runtime.bot_manager()
    if manager is not None:
        await manager.stop_bot(bot.user_id)
    await session.delete(bot)
    await session.commit()


async def set_enabled(session: AsyncSession, bot: DiscordBot, enabled: bool) -> DiscordBot:
    bot.enabled = enabled
    if not enabled:
        bot.status = BotStatus.STOPPED
    await session.commit()
    await _apply_to_manager(bot)
    return bot


async def regenerate_link_code(session: AsyncSession, bot: DiscordBot) -> DiscordBot:
    bot.link_code = generate_link_code()
    bot.discord_user_id = None
    await session.commit()
    return bot


async def _apply_to_manager(bot: DiscordBot) -> None:
    """Start or stop the live client to match the row's enabled flag."""
    manager = runtime.bot_manager()
    if manager is None:
        return
    if bot.enabled:
        await manager.restart_bot(bot.user_id)
    else:
        await manager.stop_bot(bot.user_id)
