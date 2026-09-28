"""Bot pool tests with a fake gateway client — no Discord connection."""

import asyncio
from types import SimpleNamespace

import discord
import pytest_asyncio
from sqlalchemy import select

from app import bot_messages
from app.auth.security import encrypt_bot_token
from app.bot_manager import BotManager, ManagedBot
from app.db.models import BotStatus, DiscordBot
from app.db.session import SessionFactory


class FakeGatewayClient:
    """Stands in for discord.Client: start() blocks until close()."""

    def __init__(self, fail_with: Exception | None = None):
        self.fail_with = fail_with
        self._closed = asyncio.Event()
        self._events = {}
        self.started_with: str | None = None
        self.user = "FakeBot#0001"

    def event(self, coro):
        self._events[coro.__name__] = coro
        return coro

    async def start(self, token: str):
        self.started_with = token
        if self.fail_with is not None:
            raise self.fail_with
        await self._events["on_ready"]()
        await self._closed.wait()

    def is_closed(self) -> bool:
        return self._closed.is_set()

    def is_ready(self) -> bool:
        return self.started_with is not None and not self.is_closed()

    async def close(self):
        self._closed.set()

    def get_user(self, user_id):
        return None

    async def fetch_user(self, user_id):
        outbox = []
        self.dm_outbox = outbox

        class _User:
            async def send(self, text):
                outbox.append(text)

        return _User()


class StubbedBotManager(BotManager):
    next_client_error: Exception | None = None

    def make_client(self):
        return FakeGatewayClient(fail_with=self.next_client_error)


class FakeChannel:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, text):
        self.sent.append(text)


def dm(author_id: int, content: str, *, is_bot=False) -> SimpleNamespace:
    return SimpleNamespace(
        author=SimpleNamespace(bot=is_bot, id=author_id),
        guild=None,
        content=content,
        channel=FakeChannel(),
    )


async def _bot_row(user_id: int) -> DiscordBot:
    async with SessionFactory() as session:
        return await session.scalar(
            select(DiscordBot).where(DiscordBot.user_id == user_id)
        )


@pytest_asyncio.fixture
async def bot_user(user):
    """A user with a stored (encrypted) token and a pending link code."""
    async with SessionFactory() as session:
        session.add(DiscordBot(
            user_id=user.id,
            token_encrypted=encrypt_bot_token("fake-token"),
            enabled=True,
            link_code="ABC123",
        ))
        await session.commit()
    return user


async def test_restart_and_stop_lifecycle(bot_user):
    manager = StubbedBotManager()
    await manager.restart_bot(bot_user.id)
    await asyncio.sleep(0.05)  # let the gateway task run
    assert (await _bot_row(bot_user.id)).status == BotStatus.RUNNING
    assert manager._bots[bot_user.id].client.started_with == "fake-token"

    await manager.stop_bot(bot_user.id)
    assert (await _bot_row(bot_user.id)).status == BotStatus.STOPPED
    assert bot_user.id not in manager._bots


async def test_login_failure_recorded_not_fatal(bot_user):
    manager = StubbedBotManager()
    manager.next_client_error = discord.LoginFailure("bad token")
    await manager.restart_bot(bot_user.id)
    await asyncio.sleep(0.05)
    row = await _bot_row(bot_user.id)
    assert row.status == BotStatus.ERROR
    assert "token" in row.last_error


async def test_disabled_bot_not_started(bot_user):
    async with SessionFactory() as session:
        row = await session.scalar(
            select(DiscordBot).where(DiscordBot.user_id == bot_user.id)
        )
        row.enabled = False
        await session.commit()
    manager = StubbedBotManager()
    await manager.restart_bot(bot_user.id)
    assert bot_user.id not in manager._bots


async def test_link_flow(bot_user):
    manager = StubbedBotManager()
    managed = ManagedBot(bot_user.id, manager)

    message = dm(555, "สวัสดี")  # unlinked + not a code -> instructions
    await managed.on_message(message)
    assert message.channel.sent == [bot_messages.LINK_PROMPT]

    message = dm(555, "XXXXXX")  # looks like a code but wrong
    await managed.on_message(message)
    assert message.channel.sent == [bot_messages.LINK_WRONG_CODE]

    message = dm(555, "abc 123")  # right code, case/space-insensitively
    await managed.on_message(message)
    assert message.channel.sent == [bot_messages.LINK_SUCCESS]
    row = await _bot_row(bot_user.id)
    assert row.discord_user_id == 555
    assert row.link_code is None  # one-time


async def test_link_flow_tolerates_surrounding_text(bot_user):
    """Discord includes the mention/reply text in message.content; the code
    should still be recognized instead of falling through to LINK_PROMPT."""
    manager = StubbedBotManager()
    managed = ManagedBot(bot_user.id, manager)

    message = dm(555, "<@1234567890123456> ABC123")
    await managed.on_message(message)
    assert message.channel.sent == [bot_messages.LINK_SUCCESS]
    row = await _bot_row(bot_user.id)
    assert row.discord_user_id == 555


async def test_link_flow_accepts_64_bit_snowflake(bot_user):
    """Discord author ids are 64-bit snowflakes and can exceed int32's ~2.1B
    max (e.g. 962157559370887190). The discord_user_id column must be a
    BigInteger to store them; this only actually enforces that against
    Postgres (SQLite's dynamic typing wouldn't catch a narrower column)."""
    manager = StubbedBotManager()
    managed = ManagedBot(bot_user.id, manager)

    snowflake_id = 962157559370887190
    message = dm(snowflake_id, "ABC123")
    await managed.on_message(message)
    assert message.channel.sent == [bot_messages.LINK_SUCCESS]
    row = await _bot_row(bot_user.id)
    assert row.discord_user_id == snowflake_id


async def test_linked_bot_ignores_strangers_and_answers_owner(bot_user, monkeypatch):
    # The owner's DM is routed through the intent LLM; stub it so no network
    # call happens and "รายการ" resolves to the list command.
    from app import bot_commands
    from app.llm.base import DetectedIntent
    from tests.fakes import FakeTextLLM

    monkeypatch.setattr(
        bot_commands, "get_text_llm",
        lambda: FakeTextLLM(router=DetectedIntent(command="list")),
    )

    manager = StubbedBotManager()
    managed = ManagedBot(bot_user.id, manager)
    await managed.on_message(dm(555, "ABC123"))  # link owner 555

    stranger = dm(777, "รายการ")
    await managed.on_message(stranger)
    assert stranger.channel.sent == []

    bot_msg = dm(555, "รายการ", is_bot=True)  # other bots always ignored
    await managed.on_message(bot_msg)
    assert bot_msg.channel.sent == []

    owner = dm(555, "รายการ")
    await managed.on_message(owner)
    assert len(owner.channel.sent) == 1
    assert "ไม่มีการบ้านค้างอยู่" in owner.channel.sent[0]


async def test_send_dm_requires_running_linked_bot(bot_user):
    manager = StubbedBotManager()
    assert await manager.send_dm(bot_user.id, "hi") is False  # bot not running

    await manager.restart_bot(bot_user.id)
    await asyncio.sleep(0.05)
    assert await manager.send_dm(bot_user.id, "hi") is False  # not linked yet

    async with SessionFactory() as session:
        row = await session.scalar(
            select(DiscordBot).where(DiscordBot.user_id == bot_user.id)
        )
        row.discord_user_id = 555
        await session.commit()
    assert await manager.send_dm(bot_user.id, "เตือนนะ") is True
    client = manager._bots[bot_user.id].client
    assert client.dm_outbox == ["เตือนนะ"]
    await manager.shutdown()
