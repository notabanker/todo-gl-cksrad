"""Data model for TYCHE todos.

A single ``Todo`` table backs the whole Phase 0 app. All timestamps are stored
as *naive UTC* (see ``utcnow``) so that arithmetic like ``now - created_at``
never mixes timezone-aware and naive datetimes (SQLite hands them back naive).
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
