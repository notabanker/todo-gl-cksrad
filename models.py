"""Persistent models for To-Do Gambling.

``Todo`` stores task history and the singleton ``ArcadeState`` keeps earned
Lucky Tickets across launches. All timestamps are stored as *naive UTC* (see
``utcnow``) so arithmetic never mixes timezone-aware and naive datetimes
(SQLite hands them back naive).
"""

from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
TASK_ONE_OFF = "one_off"
TASK_DAILY = "daily"


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Kept naive on purpose: SQLite returns naive datetimes on read, so storing
    naive-UTC everywhere avoids "can't subtract offset-naive and offset-aware"
    errors in the wheel weighting.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AppSettings(SQLModel, table=True):
    __tablename__ = "app_settings"

    id: int = Field(default=1, primary_key=True)
    timezone: str = Field(default="Europe/Berlin")
    updated_at: datetime = Field(default_factory=utcnow)


class Routine(SQLModel, table=True):
    __tablename__ = "routines"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    priority: int = Field(default=2)
    # Python weekday numbers: Monday=0 through Sunday=6.
    weekdays: str = Field(default="0,1,2,3,4,5,6")
    active: bool = Field(default=True)
    start_date: date = Field(default_factory=date.today)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


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
    task_kind: str = Field(default=TASK_ONE_OFF)
    routine_id: Optional[int] = Field(default=None, foreign_key="routines.id")
    occurrence_date: Optional[date] = None
    available_at: Optional[datetime] = None
    # Accumulated time deliberately hidden by "tomorrow" postponements. It is
    # excluded from the waiting-age XP bonus so deferring cannot farm XP.
    unavailable_seconds: int = Field(default=0)
    first_completed_at: Optional[datetime] = None


class ProductivityEvent(SQLModel, table=True):
    """Immutable productivity ledger entry.

    Lifetime XP is monotonic because only first-completion events carry a
    positive ``lifetime_xp_delta``. Daily score is deliberately separate and
    may be negative for a postponement.
    """

    __tablename__ = "productivity_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_key: str = Field(index=True)
    operation_id: Optional[str] = Field(default=None, index=True)
    event_type: str
    todo_id: Optional[int] = Field(default=None, index=True)
    routine_id: Optional[int] = Field(default=None, index=True)
    local_date: date = Field(index=True)
    lifetime_xp_delta: int = Field(default=0)
    daily_score_delta: int = Field(default=0)
    streak_count: int = Field(default=0)
    details_json: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class RewardGoal(SQLModel, table=True):
    __tablename__ = "reward_goals"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    cost_chips: int
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class RewardTransaction(SQLModel, table=True):
    """Append-only Reward Chip ledger; positive earns, negative redeems."""

    __tablename__ = "reward_transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_key: str = Field(index=True)
    transaction_type: str
    amount: int
    balance_after: int
    goal_id: Optional[int] = Field(default=None, index=True)
    # Snapshot the catalog title so immutable redemption history does not
    # change when a goal is renamed or deactivated later.
    goal_title: Optional[str] = None
    arcade_spin_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class ArcadeSpin(SQLModel, table=True):
    """Auditable Slot 2.0 history with the exact server-selected result."""

    __tablename__ = "arcade_spins"

    id: Optional[int] = Field(default=None, primary_key=True)
    operation_id: str = Field(index=True)
    outcome: str
    symbols_json: str
    label: str
    message: str
    tier: str
    chips_awarded: int
    spin_count: int
    tickets_remaining: int
    quote_text: str
    quote_attribution: Optional[str] = None
    local_date: date = Field(index=True)
    spun_at: datetime = Field(default_factory=utcnow)


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
