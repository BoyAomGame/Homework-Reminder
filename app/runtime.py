"""Registry for the app's long-lived singletons.

The bot manager and reminder scheduler are created once at startup
(main.py lifespan) and needed from several places — web routes, DM
command handlers, and the service layer. Registering them here avoids
circular imports between those modules.

Both getters return None before startup completes (and in unit tests
that exercise the service layer alone), so callers treat them as
optional.
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.bot_manager import BotManager
    from app.scheduler import ReminderScheduler

_bot_manager: Optional["BotManager"] = None
_scheduler: Optional["ReminderScheduler"] = None


def set_bot_manager(manager: "BotManager | None") -> None:
    global _bot_manager
    _bot_manager = manager


def bot_manager() -> Optional["BotManager"]:
    return _bot_manager


def set_scheduler(scheduler: "ReminderScheduler | None") -> None:
    global _scheduler
    _scheduler = scheduler


def scheduler() -> Optional["ReminderScheduler"]:
    return _scheduler
