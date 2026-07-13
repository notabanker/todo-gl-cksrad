"""Persistent models for To-Do Gambling.

``Todo`` stores task history and the singleton ``ArcadeState`` keeps earned
Lucky Tickets across launches. All timestamps are stored as *naive UTC* (see
``utcnow``) so arithmetic never mixes timezone-aware and naive datetimes
(SQLite hands them back naive).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

STATUS_OPEN = "open"
STATUS_DONE = "done"


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Kept naive on purpose: SQLite returns naive datetimes on read, so storing
    naive-UTC everywhere avoids "can't subtract offset-naive and offset-aware"
    errors in the wheel weighting.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Todo(SQLModel, table=True):
    __tablename__ = "todos"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: Optional[str] = None
    tags: Optional[str] = None  # raw string (e.g. comma-separated); no UI this phase
    priority: int = Field(default=2)  # 1 = high, 2 = medium, 3 = low
    status: str = Field(default=STATUS_OPEN)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None


class ArcadeState(SQLModel, table=True):
    """Persistent singleton for the XP-powered, no-loss focus arcade.

    ``highest_xp_threshold`` records the greatest 100-XP boundary that has
    already minted tickets.  It deliberately never decreases when tasks are
    reopened or deleted, which prevents the same productivity XP from earning
    a second ticket later.
    """

    __tablename__ = "arcade_state"

    id: int = Field(default=1, primary_key=True)
    highest_xp_threshold: int = Field(default=0)
    tickets: int = Field(default=0)
    spin_count: int = Field(default=0)
    last_outcome: Optional[str] = None
    last_symbols: Optional[str] = None
    last_label: Optional[str] = None
    last_message: Optional[str] = None
    last_tier: Optional[str] = None
    last_spun_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)
