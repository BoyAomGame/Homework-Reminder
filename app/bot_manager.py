"""Pool of per-user Discord bot clients.

There is no shared service bot: every user brings their own bot token,
and this module runs one discord.py gateway client per enabled token,
all on the app's event loop. Each client is created for exactly one
web-account user, so an incoming DM is attributed to its owner by
construction — no lookup by message content is ever needed.

Lifecycle:
- ``start_all_enabled`` reconnects every stored, enabled bot at app
  startup.
- ``restart_bot`` / ``stop_bot`` react to dashboard changes (token
  added or replaced, bot disabled, token removed).
- A client that fails to log in (bad token, missing intent) records the
  error on its ``discord_bots`` row for the dashboard and stays down;
  it never takes the app with it.

DM protocol per client:
- Only direct messages are handled; guild channels are ignored.
- Until the user proves ownership by DMing their one-time link code,
  nothing else is accepted.
- Once linked, only DMs from the linked Discord account are processed;
  everything is answered in Thai via app.bot_commands.
"""

import asyncio
import logging
import re

import discord
from sqlalchemy import select

from app import bot_commands, bot_messages
from app.auth.security import decrypt_bot_token
from app.db.models import BotStatus, DiscordBot
from app.db.session import SessionFactory

logger = logging.getLogger(__name__)

# A DM to an unlinked bot containing a token like this is treated as a code
# attempt, even if surrounded by other text (a mention, a reply quote, ...).
# Word boundaries keep this from matching inside a longer run of characters,
# such as the numeric id Discord embeds for an @mention.
LINK_CODE_PATTERN = re.compile(r"\b[A-Z0-9]{6}\b")


def _dm_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.dm_messages = True
    intents.message_content = True
    return intents


class ManagedBot:
    """One user's Discord client plus the task running its gateway loop."""

    def __init__(self, user_id: int, manager: "BotManager"):
        self.user_id = user_id
        self.manager = manager
        self.client = manager.make_client()
        self.task: asyncio.Task | None = None
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def on_ready(self) -> None:
        await self.manager.set_status(self.user_id, BotStatus.RUNNING)
        logger.info("Bot for user %d ready as %s", self.user_id, self.client.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is not None:
            return  # DMs only, and never other bots
        text = message.content.strip()

        async with SessionFactory() as session:
            bot_row = await session.scalar(
                select(DiscordBot).where(DiscordBot.user_id == self.user_id)
            )
            if bot_row is None:
                return

            if bot_row.discord_user_id is None:
                await self._handle_link_attempt(session, bot_row, message, text)
                return

            if message.author.id != bot_row.discord_user_id:
                return  # a stranger found this bot; stay silent

        reply = await bot_commands.handle_dm(self.user_id, text)
        await self._safe_send(message.channel, reply)

    async def _handle_link_attempt(
        self, session, bot_row: DiscordBot, message: discord.Message, text: str
    ) -> None:
        upper = text.upper()
        # A code typed with a stray internal space ("abc 123") should still
        # match once collapsed; try that first.
        collapsed = re.sub(r"\s+", "", upper)
        if LINK_CODE_PATTERN.fullmatch(collapsed):
            code = collapsed
        else:
            # Otherwise look for a standalone 6-char token anywhere in the
            # message, so a mention or reply quote around the code doesn't
            # silently swallow the attempt.
            match = LINK_CODE_PATTERN.search(upper)
            code = match.group(0) if match else None

        if code and bot_row.link_code and code == bot_row.link_code:
            bot_row.discord_user_id = message.author.id
            bot_row.link_code = None
            await session.commit()
            logger.info(
                "Bot for user %d linked to Discord account %d",
                self.user_id, message.author.id,
            )
            await self._safe_send(message.channel, bot_messages.LINK_SUCCESS)
        elif code:
            logger.info(
                "Bot for user %d got link code %r, no match (expected %r)",
                self.user_id, code, bot_row.link_code,
            )
            await self._safe_send(message.channel, bot_messages.LINK_WRONG_CODE)
        else:
            await self._safe_send(message.channel, bot_messages.LINK_PROMPT)

    async def _safe_send(self, channel: discord.abc.Messageable, text: str) -> None:
        try:
            await channel.send(text)
        except discord.DiscordException:
            logger.exception("Bot for user %d could not send a message", self.user_id)

    async def run(self, token: str) -> None:
        """Drive the gateway connection; record failures on the bot row."""
        try:
            await self.client.start(token)
            # start() returning means close() was called: a normal stop.
            await self.manager.set_status(self.user_id, BotStatus.STOPPED)
        except discord.LoginFailure:
            await self.manager.set_status(
                self.user_id, BotStatus.ERROR,
                "Discord rejected the bot token. Paste a fresh token from the Developer Portal.",
            )
        except discord.PrivilegedIntentsRequired:
            await self.manager.set_status(
                self.user_id, BotStatus.ERROR,
                "Enable the 'Message Content Intent' for your bot in the Discord Developer Portal.",
            )
        except asyncio.CancelledError:
            await self.manager.set_status(self.user_id, BotStatus.STOPPED)
            raise
        except Exception as exc:  # keep one bad bot from crashing the pool
            logger.exception("Bot for user %d crashed", self.user_id)
            await self.manager.set_status(
                self.user_id, BotStatus.ERROR, f"Unexpected error: {exc}"
            )
        finally:
            if not self.client.is_closed():
                await self.client.close()

    async def stop(self) -> None:
        if not self.client.is_closed():
            await self.client.close()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None


class BotManager:
    """Owns every running ManagedBot, keyed by web-account user id."""

    def __init__(self):
        self._bots: dict[int, ManagedBot] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def make_client() -> discord.Client:
        """Overridable in tests to run the pool without a real gateway."""
        return discord.Client(intents=_dm_intents())

    async def start_all_enabled(self) -> None:
        """Reconnect all stored, enabled bots — called once at app startup."""
        async with SessionFactory() as session:
            rows = await session.scalars(
                select(DiscordBot).where(DiscordBot.enabled.is_(True))
            )
            user_ids = [row.user_id for row in rows]
        for user_id in user_ids:
            await self.restart_bot(user_id)
        logger.info("Started %d Discord bot(s)", len(user_ids))

    async def restart_bot(self, user_id: int) -> None:
        """(Re)start one user's client from their stored token."""
        async with self._lock:
            await self._stop_locked(user_id)

            async with SessionFactory() as session:
                bot_row = await session.scalar(
                    select(DiscordBot).where(DiscordBot.user_id == user_id)
                )
                if bot_row is None or not bot_row.enabled:
                    return
                try:
                    token = decrypt_bot_token(bot_row.token_encrypted)
                except RuntimeError as exc:
                    await self._set_status_locked(user_id, BotStatus.ERROR, str(exc))
                    return

            await self._set_status_locked(user_id, BotStatus.STARTING)
            managed = ManagedBot(user_id, self)
            managed.task = asyncio.create_task(
                managed.run(token), name=f"discord-bot-user-{user_id}"
            )
            self._bots[user_id] = managed

    async def stop_bot(self, user_id: int) -> None:
        async with self._lock:
            await self._stop_locked(user_id)
            await self._set_status_locked(user_id, BotStatus.STOPPED)

    async def shutdown(self) -> None:
        async with self._lock:
            for user_id in list(self._bots):
                await self._stop_locked(user_id)

    async def send_dm(self, user_id: int, text: str) -> bool:
        """DM the linked Discord account via this user's own bot.

        Returns False when the message cannot be delivered right now
        (bot down, not linked, Discord error) so callers can decide to
        retry later instead of losing the reminder.
        """
        managed = self._bots.get(user_id)
        if managed is None or managed.client.is_closed() or not managed.client.is_ready():
            return False

        async with SessionFactory() as session:
            bot_row = await session.scalar(
                select(DiscordBot).where(DiscordBot.user_id == user_id)
            )
        if bot_row is None or bot_row.discord_user_id is None:
            return False

        try:
            recipient = managed.client.get_user(
                bot_row.discord_user_id
            ) or await managed.client.fetch_user(bot_row.discord_user_id)
            await recipient.send(text)
            return True
        except discord.DiscordException:
            logger.exception("send_dm failed for user %d", user_id)
            return False

    # --- internals -----------------------------------------------------------

    async def _stop_locked(self, user_id: int) -> None:
        managed = self._bots.pop(user_id, None)
        if managed is not None:
            await managed.stop()

    async def set_status(
        self, user_id: int, status: BotStatus, error: str | None = None
    ) -> None:
        await self._set_status_locked(user_id, status, error)

    async def _set_status_locked(
        self, user_id: int, status: BotStatus, error: str | None = None
    ) -> None:
        async with SessionFactory() as session:
            bot_row = await session.scalar(
                select(DiscordBot).where(DiscordBot.user_id == user_id)
            )
            if bot_row is not None:
                bot_row.status = status
                bot_row.last_error = error
                await session.commit()
