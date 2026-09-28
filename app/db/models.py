"""SQLAlchemy ORM models.

Every table that holds user data carries a ``user_id`` foreign key, and
all access goes through service functions that filter on it — there is
no query path that crosses user boundaries.

All datetimes are stored in UTC (timezone-aware columns).
"""

import enum
from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    """Private, editable AI configuration for the one installation."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    text_api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    text_base_url: Mapped[str] = mapped_column(String(500), default="https://api.deepseek.com/v1")
    text_model: Mapped[str] = mapped_column(String(200), default="deepseek-chat")
    vision_api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    vision_base_url: Mapped[str] = mapped_column(String(500), default="")
    vision_model: Mapped[str] = mapped_column(String(200), default="")


class AssignmentStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"


class BotStatus(str, enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_slot: Mapped[int] = mapped_column(Integer, unique=True, default=1)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Minutes before the deadline at which reminders fire, e.g. [1440, 180, 0].
    reminder_offsets: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    bot: Mapped["DiscordBot | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    timetable_entries: Mapped[list["TimetableEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class DiscordBot(Base):
    """A user's personal Discord bot.

    The token is encrypted with Fernet before it reaches this table and
    only decrypted in memory when the bot client starts.
    """

    __tablename__ = "discord_bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    token_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Discord account linked to this bot; DMs from anyone else are ignored.
    # BigInteger: Discord snowflake ids are 64-bit and overflow plain int32.
    discord_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # One-time code the user DMs to the bot to prove ownership of the account.
    link_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    status: Mapped[BotStatus] = mapped_column(
        Enum(BotStatus, values_callable=lambda e: [m.value for m in e]),
        default=BotStatus.STOPPED,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="bot")


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subject_name: Mapped[str] = mapped_column(String(200))
    teacher_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Free text: "book page 5", "worksheet", "Google form", ...
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    due_date: Mapped[date] = mapped_column(Date)
    # Optional; when null the deadline is the end of due_date (Bangkok time).
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus, values_callable=lambda e: [m.value for m in e]),
        default=AssignmentStatus.PENDING,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Reserved for future recurring-assignment support; not implemented yet.
    recurrence_rule: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Reminder state: offsets (minutes before deadline) that already fired.
    reminders_sent: Mapped[list[int]] = mapped_column(JSON, default=list)
    overdue_warned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    overdue_warn_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="assignments")


class TimetableEntry(Base):
    """One class meeting in a user's weekly timetable.

    ``start_time`` may be null, in which case a default is computed from
    ``period_number`` (period 1 starts 08:30, ~50 minutes each) — used
    only until the user configures their own times.
    """

    __tablename__ = "timetable_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "day_of_week", "period_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0 = Monday ... 6 = Sunday
    period_number: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    subject_name: Mapped[str] = mapped_column(String(200))
    teacher_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    room: Mapped[str | None] = mapped_column(String(200), nullable=True)

    user: Mapped[User] = relationship(back_populates="timetable_entries")
