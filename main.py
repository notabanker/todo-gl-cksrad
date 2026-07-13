"""To-Do Gambling — FastAPI app.

Add tasks, manage them in a list (edit / delete / priority), and spin the wheel
for an age-weighted random open task.

Run standalone with ``python main.py`` (boots uvicorn and opens the browser) or
via ``uvicorn main:app --reload``.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Literal, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

import wheel
from database import get_session, init_db
from migrations import system_timezone_name
from models import (
    AppSettings,
    ArcadeSpin,
    ArcadeState,
    ProductivityEvent,
    RewardGoal,
    RewardTransaction,
    Routine,
    STATUS_DONE,
    STATUS_OPEN,
    STATUS_SKIPPED,
    TASK_DAILY,
    TASK_ONE_OFF,
    Todo,
    utcnow,
)

PRIORITY_MIN, PRIORITY_MAX = 1, 3
XP_PER_TICKET = 100
POSTPONE_PENALTY = -10
MAX_DAILY_REWARD_SPINS = 3

# The reels are prompts, never wagers: every outcome sends the user back to a
# positive, bounded focus action.  Selection happens only on the server using
# ``SystemRandom`` so the browser cannot nominate its own result.
ARCADE_OUTCOMES = (
    {
        "outcome": "quick_win",
        "weight": 50,
        "symbols": ["⚡", "✅", "🎯"],
        "label": "Quick Win",
        "message": "Crush one task that takes under two minutes.",
        "tier": "common",
        "chips": 5,
    },
    {
        "outcome": "focus_15",
        "weight": 30,
        "symbols": ["🎯", "⏱️", "🎯"],
        "label": "15-Minute Focus",
        "message": "Choose one task and give it 15 distraction-free minutes.",
        "tier": "common",
        "chips": 10,
    },
    {
        "outcome": "focus_25",
        "weight": 15,
        "symbols": ["🔥", "🧠", "🔥"],
        "label": "25-Minute Power Block",
        "message": "Take your most important task through one 25-minute focus block.",
        "tier": "uncommon",
        "chips": 20,
    },
    {
        "outcome": "break_5",
        "weight": 4,
        "symbols": ["☕", "🌿", "☕"],
        "label": "5-Minute Reset",
        "message": "Take five intentional minutes, then return refreshed to one task.",
        "tier": "rare",
        "chips": 50,
    },
    {
        "outcome": "jackpot",
        "weight": 1,
        "symbols": ["7️⃣", "7️⃣", "7️⃣"],
        "label": "Focus Jackpot",
        "message": "Clear the runway and give your top task one heroic focus block.",
        "tier": "jackpot",
        "chips": 100,
    },
)
arcade_rng = secrets.SystemRandom()
quote_rng = secrets.SystemRandom()
MOTIVATIONAL_QUOTES = (
    ("Nicht nachdenken. Einen Schritt machen.", None),
    ("Dein Kopf verhandelt. Du fängst trotzdem an.", None),
    ("Unperfekt erledigt schlägt perfekt verschoben.", None),
    ("Eine Fokus-Runde. Dann darfst du neu entscheiden.", None),
    ("Mach es deinem Morgen nicht noch schwerer.", None),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tests replace the session dependency with an isolated engine. Avoid
    # migrating the user's real database while such an override is active.
    if get_session not in app.dependency_overrides:
        init_db()
    yield


app = FastAPI(title="To-Do Gambling", lifespan=lifespan)

# PyInstaller exposes bundled data through sys._MEIPASS. In development this
# resolves to the repository, so the existing ./run.sh workflow is unchanged.
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
templates = Jinja2Templates(directory=str(RESOURCE_ROOT / "templates"))


# --- request schemas ---------------------------------------------------------

class TodoCreate(BaseModel):
    title: str
    priority: int = 2


class TodoUpdate(BaseModel):
    title: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[Literal["open", "done"]] = None


class OperationRequest(BaseModel):
    operation_id: Optional[str] = None


class PostponeResponse(BaseModel):
    todo: Todo
    postponed_to: datetime
    local_date: date
    daily_score_delta: int
    daily_score: int
    idempotent: bool


class RoutineCreate(BaseModel):
    title: str
    priority: int = 2
    weekdays: Optional[list[int]] = None


class RoutineUpdate(BaseModel):
    title: Optional[str] = None
    priority: Optional[int] = None
    weekdays: Optional[list[int]] = None
    active: Optional[bool] = None


class RoutineResponse(BaseModel):
    id: int
    title: str
    priority: int
    weekdays: list[int]
    active: bool
    start_date: date
    current_streak: int
    created_at: datetime
    updated_at: datetime


class QuoteResponse(BaseModel):
    text: str
    attribution: Optional[str] = None


class ArcadeSpinResult(BaseModel):
    symbols: list[str]
    outcome: str
    label: str
    message: str
    tier: str
    spin_count: int
    spun_at: datetime
    chips_awarded: int = 0
    quote: Optional[QuoteResponse] = None
    operation_id: Optional[str] = None


class ArcadeStatusResponse(BaseModel):
    tickets: int
    task_xp: int
    xp_to_next_ticket: int
    last_spin: Optional[ArcadeSpinResult]
    lifetime_xp: int
    daily_score: int
    chips: int
    reward_spins_today: int
    reward_spins_remaining: int
    max_daily_reward_spins: int = MAX_DAILY_REWARD_SPINS


class ArcadeSpinResponse(ArcadeSpinResult):
    remaining_tickets: int
    chips_balance: int
    reward_spins_remaining: int
    idempotent: bool = False


class SettingsUpdate(BaseModel):
    timezone: str


class RewardGoalCreate(BaseModel):
    title: str
    cost_chips: int


class RewardGoalUpdate(BaseModel):
    title: Optional[str] = None
    cost_chips: Optional[int] = None
    active: Optional[bool] = None


class RewardGoalResponse(BaseModel):
    id: int
    title: str
    cost_chips: int
    active: bool
    affordable: bool
    progress_chips: int
    progress_percent: int
    times_redeemed: int
    created_at: datetime
    updated_at: datetime


class RewardTransactionResponse(BaseModel):
    id: int
    transaction_type: str
    amount: int
    balance_after: int
    goal_id: Optional[int]
    goal_title: Optional[str]
    created_at: datetime


class RewardOverviewResponse(BaseModel):
    chips: int
    lifetime_chips_earned: int
    goals: list[RewardGoalResponse]
    recent_transactions: list[RewardTransactionResponse]


class RewardRedeemResponse(BaseModel):
    goal: RewardGoalResponse
    transaction: RewardTransactionResponse
    chips_balance: int
    idempotent: bool


# --- helpers -----------------------------------------------------------------

def _validate_priority(priority: int) -> int:
    if not PRIORITY_MIN <= priority <= PRIORITY_MAX:
        raise HTTPException(status_code=422, detail="priority must be 1, 2, or 3")
    return priority


def _get_or_404(session: Session, todo_id: int) -> Todo:
    todo = session.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="todo not found")
    return todo


def _settings(session: Session) -> AppSettings:
    settings = session.get(AppSettings, 1)
    if settings is None:
        settings = AppSettings(id=1, timezone=system_timezone_name())
        session.add(settings)
        session.flush()
    return settings


def _zone(session: Session) -> ZoneInfo:
    settings = _settings(session)
    try:
        return ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        settings.timezone = "Europe/Berlin"
        settings.updated_at = utcnow()
        session.add(settings)
        session.flush()
        return ZoneInfo("Europe/Berlin")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_date(session: Session, now: Optional[datetime] = None) -> date:
    return _aware_utc(now or utcnow()).astimezone(_zone(session)).date()


def _local_midnight_utc(day: date, zone: ZoneInfo) -> datetime:
    local = datetime.combine(day, time.min, tzinfo=zone)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _day_bounds_utc(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    return _local_midnight_utc(day, zone), _local_midnight_utc(day + timedelta(days=1), zone)


def _weekdays_list(value: str) -> list[int]:
    try:
        result = sorted({int(item) for item in value.split(",") if item != ""})
    except ValueError:
        return list(range(7))
    return result if result and all(0 <= item <= 6 for item in result) else list(range(7))


def _weekdays_value(days: Optional[list[int]]) -> str:
    normalized = list(range(7)) if days is None else sorted(set(days))
    if not normalized or any(day < 0 or day > 6 for day in normalized):
        raise HTTPException(status_code=422, detail="weekdays must contain values from 0 to 6")
    return ",".join(str(day) for day in normalized)


def _routine_runs_on(routine: Routine, day: date) -> bool:
    return routine.active and day >= routine.start_date and day.weekday() in _weekdays_list(routine.weekdays)


def _daily_occurrence_is_scheduled(session: Session, todo: Todo) -> bool:
    if todo.task_kind != TASK_DAILY:
        return True
    if todo.routine_id is None or todo.occurrence_date is None:
        return False
    routine = session.get(Routine, todo.routine_id)
    return routine is not None and _routine_runs_on(routine, todo.occurrence_date)


def _ensure_routine_occurrences(session: Session, day: date) -> None:
    zone = _zone(session)
    created_at = _local_midnight_utc(day, zone)
    routines = session.exec(select(Routine).where(Routine.active == True)).all()  # noqa: E712
    for routine in routines:
        if not _routine_runs_on(routine, day):
            continue
        existing = session.exec(
            select(Todo).where(
                Todo.routine_id == routine.id,
                Todo.occurrence_date == day,
            )
        ).first()
        if existing is not None:
            if existing.status == STATUS_OPEN and (
                existing.title != routine.title
                or existing.priority != routine.priority
            ):
                existing.title = routine.title
                existing.priority = routine.priority
                existing.updated_at = utcnow()
                session.add(existing)
            continue
        session.add(
            Todo(
                title=routine.title,
                priority=routine.priority,
                status=STATUS_OPEN,
                task_kind=TASK_DAILY,
                routine_id=routine.id,
                occurrence_date=day,
                available_at=created_at,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    session.flush()


def _open_todos(session: Session, now: Optional[datetime] = None) -> list[Todo]:
    """Currently actionable tasks, including today's daily occurrences."""
    current = now or utcnow()
    today = _local_date(session, current)
    _ensure_routine_occurrences(session, today)
    todos = session.exec(
        select(Todo)
        .where(Todo.status == STATUS_OPEN)
        .order_by(Todo.priority, Todo.created_at)
    ).all()
    return [
        todo
        for todo in todos
        if (todo.available_at is None or todo.available_at <= current)
        and (
            todo.task_kind != TASK_DAILY
            or (
                todo.occurrence_date == today
                and _daily_occurrence_is_scheduled(session, todo)
            )
        )
    ]


def _tomorrow_todos(session: Session, now: Optional[datetime] = None) -> list[Todo]:
    current = now or utcnow()
    zone = _zone(session)
    tomorrow = _local_date(session, current) + timedelta(days=1)
    _ensure_routine_occurrences(session, tomorrow)
    start, end = _day_bounds_utc(tomorrow, zone)
    todos = session.exec(
        select(Todo)
        .where(Todo.status == STATUS_OPEN)
        .order_by(Todo.priority, Todo.created_at)
    ).all()
    return [
        todo
        for todo in todos
        if (
            todo.task_kind == TASK_DAILY
            and todo.occurrence_date == tomorrow
            and _daily_occurrence_is_scheduled(session, todo)
        )
        or (
            todo.task_kind != TASK_DAILY
            and todo.available_at is not None
            and start <= todo.available_at < end
        )
    ]


def _daily_todos(session: Session, day: date) -> list[Todo]:
    _ensure_routine_occurrences(session, day)
    return list(
        session.exec(
            select(Todo)
            .where(Todo.task_kind == TASK_DAILY, Todo.occurrence_date == day)
            .order_by(Todo.priority, Todo.created_at)
        )
    )


def _done_todos(session: Session) -> list[Todo]:
    return list(
        session.exec(
            select(Todo)
            .where(Todo.status == STATUS_DONE)
            .order_by(Todo.completed_at.desc(), Todo.updated_at.desc(), Todo.id.desc())
        )
    )


def _task_points(todo: Todo, completed: Optional[datetime] = None) -> int:
    """Current task-list XP formula; dailies never receive waiting XP."""
    priority_base = {1: 50, 2: 30, 3: 20}.get(todo.priority, 20)
    if todo.task_kind == TASK_DAILY:
        return priority_base
    completed_at = completed or todo.completed_at or todo.updated_at
    active_wait_seconds = max(
        0,
        (completed_at - todo.created_at).total_seconds()
        - max(0, todo.unavailable_seconds),
    )
    waited_days = int(active_wait_seconds // 86400)
    return priority_base + min(30, waited_days * 2)


def _task_instance_token(todo: Todo) -> str:
    created = todo.created_at.isoformat(timespec="microseconds")
    return f"{todo.id}:created:{created}"


def _completion_event_key(todo: Todo) -> str:
    if todo.task_kind == TASK_DAILY and todo.routine_id and todo.occurrence_date:
        return (
            f"routine:{todo.routine_id}:occurrence:"
            f"{todo.occurrence_date.isoformat()}:completion"
        )
    return f"todo:{_task_instance_token(todo)}:completion"


def _postpone_event_key(todo: Todo, day: date) -> str:
    if todo.task_kind == TASK_DAILY and todo.routine_id and todo.occurrence_date:
        return (
            f"routine:{todo.routine_id}:occurrence:"
            f"{todo.occurrence_date.isoformat()}:postpone"
        )
    return f"todo:{_task_instance_token(todo)}:postpone:{day.isoformat()}"


def _event_by_key(session: Session, key: str) -> Optional[ProductivityEvent]:
    return session.exec(
        select(ProductivityEvent).where(ProductivityEvent.event_key == key)
    ).first()


def _previous_scheduled_date(routine: Routine, day: date) -> Optional[date]:
    cursor = day - timedelta(days=1)
    weekdays = set(_weekdays_list(routine.weekdays))
    for _ in range(14):
        if cursor < routine.start_date:
            return None
        if cursor.weekday() in weekdays:
            return cursor
        cursor -= timedelta(days=1)
    return None


def _streak_before(session: Session, routine: Routine, day: date) -> int:
    previous = _previous_scheduled_date(routine, day)
    if previous is None:
        return 0
    event = session.exec(
        select(ProductivityEvent)
        .where(
            ProductivityEvent.routine_id == routine.id,
            ProductivityEvent.local_date == previous,
            ProductivityEvent.lifetime_xp_delta > 0,
        )
        .order_by(ProductivityEvent.id.desc())
    ).first()
    return event.streak_count if event is not None else 0


def _award_completion(
    session: Session, todo: Todo, completed_at: Optional[datetime] = None
) -> tuple[ProductivityEvent, bool]:
    key = _completion_event_key(todo)
    existing = _event_by_key(session, key)
    if existing is not None:
        return existing, False
    completed = completed_at or todo.completed_at or utcnow()
    if todo.first_completed_at is None:
        todo.first_completed_at = completed
        session.add(todo)
    if todo.task_kind == TASK_DAILY and todo.routine_id and todo.occurrence_date:
        routine = session.get(Routine, todo.routine_id)
        streak = (_streak_before(session, routine, todo.occurrence_date) + 1) if routine else 1
        base = {1: 50, 2: 30, 3: 20}.get(todo.priority, 20)
        points = base + min(streak, 10)
        scoring_date = todo.occurrence_date
        event_type = "daily_completion"
        details = {"priority_base": base, "streak_bonus": min(streak, 10)}
    else:
        streak = 0
        points = _task_points(todo, completed)
        scoring_date = _aware_utc(completed).astimezone(_zone(session)).date()
        event_type = "completion"
        details = {"points": points}
    event = ProductivityEvent(
        event_key=key,
        event_type=event_type,
        todo_id=todo.id,
        routine_id=todo.routine_id,
        local_date=scoring_date,
        lifetime_xp_delta=points,
        daily_score_delta=points,
        streak_count=streak,
        details_json=json.dumps(details, separators=(",", ":")),
        created_at=completed,
    )
    session.add(event)
    session.flush()
    return event, True


def _ensure_completion_awards(session: Session) -> None:
    completed = session.exec(select(Todo).where(Todo.status == STATUS_DONE)).all()
    for todo in completed:
        _award_completion(session, todo, todo.first_completed_at or todo.completed_at or todo.updated_at)
    session.flush()


def _current_task_xp(session: Session) -> int:
    session.flush()
    return sum(_task_points(todo) for todo in _done_todos(session))


def _lifetime_xp(session: Session) -> int:
    _ensure_completion_awards(session)
    value = session.exec(select(func.coalesce(func.sum(ProductivityEvent.lifetime_xp_delta), 0))).one()
    return int(value)


def _daily_score(session: Session, day: date) -> int:
    value = session.exec(
        select(func.coalesce(func.sum(ProductivityEvent.daily_score_delta), 0)).where(
            ProductivityEvent.local_date == day
        )
    ).one()
    return int(value)


def _completion_streak(session: Session, today: date) -> int:
    """Consecutive productive local dates, sourced only from the ledger.

    A still-open current day does not erase yesterday's streak. Once today has
    a completion it extends the sequence; otherwise we count backwards from
    yesterday.
    """
    rows = session.exec(
        select(ProductivityEvent.local_date)
        .where(
            ProductivityEvent.event_type.in_(
                ("completion", "daily_completion", "legacy_completion")
            ),
            ProductivityEvent.local_date <= today,
        )
        .distinct()
        .order_by(ProductivityEvent.local_date.desc())
    ).all()
    completed_days = set(rows)
    cursor = today if today in completed_days else today - timedelta(days=1)
    streak = 0
    while cursor in completed_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _chips_balance(session: Session) -> int:
    value = session.exec(select(func.coalesce(func.sum(RewardTransaction.amount), 0))).one()
    return int(value)


def _lifetime_chips(session: Session) -> int:
    value = session.exec(
        select(func.coalesce(func.sum(RewardTransaction.amount), 0)).where(
            RewardTransaction.amount > 0
        )
    ).one()
    return int(value)


def _arcade_state(session: Session) -> ArcadeState:
    state = session.get(ArcadeState, 1)
    if state is None:
        state = ArcadeState(id=1)
        session.add(state)
        session.flush()
    return state


def _reconcile_arcade(session: Session) -> tuple[ArcadeState, int, int]:
    """Mint only newly crossed monotonic lifetime-XP boundaries."""
    state = _arcade_state(session)
    task_xp = _current_task_xp(session)
    lifetime_xp = _lifetime_xp(session)
    reached_threshold = (lifetime_xp // XP_PER_TICKET) * XP_PER_TICKET
    if reached_threshold > state.highest_xp_threshold:
        state.tickets += (reached_threshold - state.highest_xp_threshold) // XP_PER_TICKET
        state.highest_xp_threshold = reached_threshold
        state.updated_at = utcnow()
        session.add(state)
    return state, task_xp, lifetime_xp


def _spins_today(session: Session, day: date) -> int:
    value = session.exec(
        select(func.count(ArcadeSpin.id)).where(ArcadeSpin.local_date == day)
    ).one()
    return int(value)


def _last_spin(session: Session, state: ArcadeState) -> Optional[ArcadeSpinResult]:
    latest = session.exec(select(ArcadeSpin).order_by(ArcadeSpin.id.desc())).first()
    if latest is not None:
        return ArcadeSpinResult(
            symbols=list(json.loads(latest.symbols_json)),
            outcome=latest.outcome,
            label=latest.label,
            message=latest.message,
            tier=latest.tier,
            spin_count=latest.spin_count,
            spun_at=latest.spun_at,
            chips_awarded=latest.chips_awarded,
            quote=(
                QuoteResponse(
                    text=latest.quote_text,
                    attribution=latest.quote_attribution,
                )
                if latest.quote_text
                else None
            ),
            operation_id=latest.operation_id,
        )
    if not all((state.last_outcome, state.last_symbols, state.last_label, state.last_message, state.last_tier, state.last_spun_at)):
        return None
    try:
        symbols = json.loads(state.last_symbols)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(symbols, list) or len(symbols) != 3:
        return None
    return ArcadeSpinResult(
        symbols=[str(symbol) for symbol in symbols],
        outcome=state.last_outcome,
        label=state.last_label,
        message=state.last_message,
        tier=state.last_tier,
        spin_count=state.spin_count,
        spun_at=state.last_spun_at,
    )


def _arcade_status(
    session: Session, state: ArcadeState, task_xp: int, lifetime_xp: int, day: date
) -> ArcadeStatusResponse:
    spins = _spins_today(session, day)
    next_threshold = state.highest_xp_threshold + XP_PER_TICKET
    return ArcadeStatusResponse(
        tickets=state.tickets,
        task_xp=task_xp,
        xp_to_next_ticket=max(0, next_threshold - lifetime_xp),
        last_spin=_last_spin(session, state),
        lifetime_xp=lifetime_xp,
        daily_score=_daily_score(session, day),
        chips=_chips_balance(session),
        reward_spins_today=spins,
        reward_spins_remaining=max(0, MAX_DAILY_REWARD_SPINS - spins),
    )


def _routine_current_streak(session: Session, routine: Routine, today: date) -> int:
    if not routine.active:
        return 0
    today_occurrence = session.exec(
        select(Todo).where(Todo.routine_id == routine.id, Todo.occurrence_date == today)
    ).first()
    if today_occurrence is not None and today_occurrence.status == STATUS_SKIPPED:
        return 0
    today_event = session.exec(
        select(ProductivityEvent).where(
            ProductivityEvent.routine_id == routine.id,
            ProductivityEvent.local_date == today,
            ProductivityEvent.lifetime_xp_delta > 0,
        )
    ).first()
    if today_event is not None:
        return today_event.streak_count
    return _streak_before(session, routine, today)


def _routine_response(session: Session, routine: Routine, today: date) -> RoutineResponse:
    return RoutineResponse(
        id=routine.id,
        title=routine.title,
        priority=routine.priority,
        weekdays=_weekdays_list(routine.weekdays),
        active=routine.active,
        start_date=routine.start_date,
        current_streak=_routine_current_streak(session, routine, today),
        created_at=routine.created_at,
        updated_at=routine.updated_at,
    )


def _goal_response(session: Session, goal: RewardGoal, balance: int) -> RewardGoalResponse:
    redeemed = session.exec(
        select(func.count(RewardTransaction.id)).where(
            RewardTransaction.goal_id == goal.id,
            RewardTransaction.transaction_type == "redeem",
        )
    ).one()
    return RewardGoalResponse(
        id=goal.id,
        title=goal.title,
        cost_chips=goal.cost_chips,
        active=goal.active,
        affordable=balance >= goal.cost_chips,
        progress_chips=min(balance, goal.cost_chips),
        progress_percent=min(100, int((balance / goal.cost_chips) * 100)),
        times_redeemed=int(redeemed),
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


def _transaction_response(session: Session, transaction: RewardTransaction) -> RewardTransactionResponse:
    goal = session.get(RewardGoal, transaction.goal_id) if transaction.goal_id else None
    return RewardTransactionResponse(
        id=transaction.id,
        transaction_type=transaction.transaction_type,
        amount=transaction.amount,
        balance_after=transaction.balance_after,
        goal_id=transaction.goal_id,
        goal_title=transaction.goal_title or (goal.title if goal else None),
        created_at=transaction.created_at,
    )


def _spin_response(
    session: Session, spin: ArcadeSpin, *, idempotent: bool
) -> ArcadeSpinResponse:
    current_day = _local_date(session)
    spins = _spins_today(session, current_day)
    state = _arcade_state(session)
    return ArcadeSpinResponse(
        symbols=[str(symbol) for symbol in json.loads(spin.symbols_json)],
        outcome=spin.outcome,
        label=spin.label,
        message=spin.message,
        tier=spin.tier,
        spin_count=spin.spin_count,
        spun_at=spin.spun_at,
        chips_awarded=spin.chips_awarded,
        quote=(
            QuoteResponse(text=spin.quote_text, attribution=spin.quote_attribution)
            if spin.quote_text
            else None
        ),
        operation_id=spin.operation_id,
        remaining_tickets=state.tickets,
        chips_balance=_chips_balance(session),
        reward_spins_remaining=max(0, MAX_DAILY_REWARD_SPINS - spins),
        idempotent=idempotent,
    )


def _begin_immediate(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _commit_and_refresh_todos(session: Session, todos: list[Todo]) -> list[Todo]:
    """Keep ORM rows serializable after a GET materializes routine entries."""
    session.commit()
    for todo in todos:
        session.refresh(todo)
    return todos


# --- routes ------------------------------------------------------------------

@app.get("/")
def index(request: Request):
    # Keyword arguments work across both the existing Starlette version and
    # the newer bundled desktop runtime.
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Readiness endpoint used by the native macOS launcher."""
    return {"status": "ok"}


@app.get("/todos")
def list_todos(
    status: Literal["open", "done"] = STATUS_OPEN,
    session: Session = Depends(get_session),
) -> list[Todo]:
    """Legacy-compatible list; open now means currently actionable today."""
    if status == STATUS_DONE:
        return _done_todos(session)
    _begin_immediate(session)
    todos = _open_todos(session)
    return _commit_and_refresh_todos(session, todos)


@app.get("/today")
def list_today(session: Session = Depends(get_session)) -> list[Todo]:
    _begin_immediate(session)
    todos = _open_todos(session)
    return _commit_and_refresh_todos(session, todos)


@app.get("/tomorrow")
def list_tomorrow(session: Session = Depends(get_session)) -> list[Todo]:
    _begin_immediate(session)
    todos = _tomorrow_todos(session)
    return _commit_and_refresh_todos(session, todos)


@app.get("/dailies")
def list_dailies(
    day: Optional[date] = Query(default=None, alias="date"),
    session: Session = Depends(get_session),
) -> list[Todo]:
    _begin_immediate(session)
    today = _local_date(session)
    selected = day or today
    if selected not in {today, today + timedelta(days=1)}:
        session.rollback()
        raise HTTPException(status_code=422, detail="dailies date must be today or tomorrow")
    todos = _daily_todos(session, selected)
    return _commit_and_refresh_todos(session, todos)


@app.post("/todos", status_code=201)
def create_todo(
    payload: TodoCreate, session: Session = Depends(get_session)
) -> Todo:
    """Create a todo from JSON. Returns the created todo."""
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    _begin_immediate(session)
    todo = Todo(
        title=title,
        priority=_validate_priority(payload.priority),
        status=STATUS_OPEN,
        task_kind=TASK_ONE_OFF,
    )
    session.add(todo)
    session.commit()
    session.refresh(todo)
    return todo


@app.patch("/todos/{todo_id}")
def update_todo(
    todo_id: int, payload: TodoUpdate, session: Session = Depends(get_session)
) -> Todo:
    """Edit a todo's title, priority, and/or completion status."""
    _begin_immediate(session)
    todo = _get_or_404(session, todo_id)
    if todo.task_kind == TASK_DAILY and (
        payload.title is not None or payload.priority is not None
    ):
        session.rollback()
        routine_target = (
            f"PATCH /routines/{todo.routine_id}"
            if todo.routine_id is not None
            else "the routine endpoint"
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "daily occurrence titles and priorities are managed by their "
                f"routine; update {routine_target} instead"
            ),
        )
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="title is required")
        todo.title = title
    if payload.priority is not None:
        todo.priority = _validate_priority(payload.priority)
    if payload.status is not None and payload.status != todo.status:
        now = utcnow()
        if todo.task_kind == TASK_DAILY and todo.status in {STATUS_DONE, STATUS_SKIPPED}:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="a completed or skipped daily occurrence is immutable",
            )
        if todo.status == STATUS_DONE and payload.status == STATUS_OPEN:
            # A legacy/direct-seeded completion may not have reached the
            # append-only ledger yet. Preserve its lifetime XP/high-water
            # before removing it from the current done-task view.
            _award_completion(
                session,
                todo,
                todo.first_completed_at or todo.completed_at or todo.updated_at,
            )
            _reconcile_arcade(session)
        if payload.status == STATUS_DONE:
            if todo.status == STATUS_SKIPPED:
                session.rollback()
                raise HTTPException(status_code=409, detail="a skipped daily cannot be completed")
            if todo.available_at is not None and todo.available_at > now:
                session.rollback()
                raise HTTPException(status_code=409, detail="postponed task is not available yet")
            if todo.task_kind == TASK_DAILY and todo.occurrence_date != _local_date(session, now):
                session.rollback()
                raise HTTPException(status_code=409, detail="daily occurrence is not active today")
            if todo.task_kind == TASK_DAILY and not _daily_occurrence_is_scheduled(session, todo):
                session.rollback()
                raise HTTPException(status_code=409, detail="daily routine is not active today")
        todo.status = payload.status
        todo.completed_at = now if payload.status == STATUS_DONE else None
        if payload.status == STATUS_DONE:
            if todo.first_completed_at is None:
                todo.first_completed_at = now
            session.add(todo)
            session.flush()
            _award_completion(session, todo, now)
    todo.updated_at = utcnow()
    session.add(todo)
    if todo.status == STATUS_DONE:
        _reconcile_arcade(session)
    session.commit()
    session.refresh(todo)
    return todo


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int, session: Session = Depends(get_session)) -> Response:
    """Hard-delete a todo."""
    _begin_immediate(session)
    todo = _get_or_404(session, todo_id)
    if todo.task_kind == TASK_DAILY:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="daily occurrences are managed by their routine",
        )
    if todo.status == STATUS_DONE:
        _award_completion(session, todo, todo.first_completed_at or todo.completed_at)
        _reconcile_arcade(session)
    session.delete(todo)
    session.commit()
    return Response(status_code=204)


@app.post("/todos/{todo_id}/postpone", response_model=PostponeResponse)
def postpone_todo(
    todo_id: int,
    payload: Optional[OperationRequest] = None,
    session: Session = Depends(get_session),
) -> PostponeResponse:
    _begin_immediate(session)
    todo = _get_or_404(session, todo_id)
    operation_id = None
    if payload is not None and payload.operation_id is not None:
        operation_id = payload.operation_id.strip()
        if not operation_id:
            session.rollback()
            raise HTTPException(status_code=422, detail="operation_id cannot be blank")
        existing = session.exec(
            select(ProductivityEvent).where(
                ProductivityEvent.operation_id == operation_id
            )
        ).first()
        existing_token = None
        if existing is not None and existing.details_json:
            try:
                existing_token = json.loads(existing.details_json).get("task_instance")
            except (json.JSONDecodeError, TypeError):
                pass
        if existing is not None and (
            existing.todo_id != todo_id
            or existing_token != _task_instance_token(todo)
        ):
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="operation_id was used for another todo",
            )
    else:
        existing = None
    now = utcnow()
    today = _local_date(session, now)
    semantic_key = _postpone_event_key(todo, today)
    if existing is None:
        # Enforce the semantic one-postpone-per-task/local-day rule even if a
        # client accidentally sends a fresh operation id on retry.
        existing = _event_by_key(session, semantic_key)
    next_midnight = _local_midnight_utc(today + timedelta(days=1), _zone(session))
    if existing is not None:
        score = _daily_score(session, existing.local_date)
        postponed_to = todo.available_at
        if existing.details_json:
            try:
                saved = json.loads(existing.details_json).get("postponed_to")
                if saved:
                    postponed_to = datetime.fromisoformat(saved)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        if postponed_to is None:
            postponed_to = _local_midnight_utc(
                existing.local_date + timedelta(days=1), _zone(session)
            )
        session.commit()
        session.refresh(todo)
        return PostponeResponse(
            todo=todo,
            postponed_to=postponed_to,
            local_date=existing.local_date,
            daily_score_delta=existing.daily_score_delta,
            daily_score=score,
            idempotent=True,
        )
    if todo.status != STATUS_OPEN:
        session.rollback()
        raise HTTPException(status_code=409, detail="only an open task can be postponed")
    if todo.available_at is not None and todo.available_at > now:
        session.rollback()
        raise HTTPException(status_code=409, detail="task is already postponed")
    if todo.task_kind == TASK_DAILY and todo.occurrence_date != today:
        session.rollback()
        raise HTTPException(status_code=409, detail="only today's daily can be skipped")
    if todo.task_kind == TASK_DAILY and not _daily_occurrence_is_scheduled(session, todo):
        session.rollback()
        raise HTTPException(status_code=409, detail="daily routine is not active today")

    todo.available_at = next_midnight
    todo.unavailable_seconds += max(0, int((next_midnight - now).total_seconds()))
    if todo.task_kind == TASK_DAILY:
        # The template creates tomorrow's one normal occurrence. Carrying this
        # occurrence forward as well would duplicate the daily.
        todo.status = STATUS_SKIPPED
    todo.updated_at = now
    session.add(todo)
    session.add(
        ProductivityEvent(
            event_key=semantic_key,
            operation_id=operation_id,
            event_type="postpone",
            todo_id=todo.id,
            routine_id=todo.routine_id,
            local_date=today,
            daily_score_delta=POSTPONE_PENALTY,
            details_json=json.dumps(
                {
                    "operation_id": operation_id,
                    "task_instance": _task_instance_token(todo),
                    "postponed_to": next_midnight.isoformat(),
                },
                separators=(",", ":"),
            ),
            created_at=now,
        )
    )
    session.flush()
    score = _daily_score(session, today)
    session.commit()
    session.refresh(todo)
    return PostponeResponse(
        todo=todo,
        postponed_to=next_midnight,
        local_date=today,
        daily_score_delta=POSTPONE_PENALTY,
        daily_score=score,
        idempotent=False,
    )


@app.get("/settings")
def get_settings(session: Session = Depends(get_session)) -> dict[str, str]:
    _begin_immediate(session)
    settings = _settings(session)
    response = {"timezone": settings.timezone}
    session.commit()
    return response


@app.patch("/settings")
def update_settings(
    payload: SettingsUpdate, session: Session = Depends(get_session)
) -> dict[str, str]:
    try:
        ZoneInfo(payload.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail="timezone must be a valid IANA name")
    _begin_immediate(session)
    settings = _settings(session)
    now = utcnow()
    old_day = _local_date(session, now)
    new_day = _aware_utc(now).astimezone(ZoneInfo(payload.timezone)).date()
    if new_day != old_day:
        productive_activity = session.exec(
            select(func.count(ProductivityEvent.id)).where(
                ProductivityEvent.local_date == old_day,
                ProductivityEvent.event_type.in_(
                    (
                        "completion",
                        "daily_completion",
                        "legacy_completion",
                        "postpone",
                    )
                ),
            )
        ).one()
        if int(productive_activity) > 0 or _spins_today(session, old_day) > 0:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="timezone cannot change the active day after today's activity",
            )
    settings.timezone = payload.timezone
    settings.updated_at = now
    session.add(settings)
    session.commit()
    return {"timezone": payload.timezone}


@app.get("/routines", response_model=list[RoutineResponse])
def list_routines(session: Session = Depends(get_session)) -> list[RoutineResponse]:
    _begin_immediate(session)
    today = _local_date(session)
    _ensure_routine_occurrences(session, today)
    routines = session.exec(select(Routine).order_by(Routine.id)).all()
    response = [_routine_response(session, routine, today) for routine in routines]
    session.commit()
    return response


@app.post("/routines", status_code=201, response_model=RoutineResponse)
def create_routine(
    payload: RoutineCreate, session: Session = Depends(get_session)
) -> RoutineResponse:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    _begin_immediate(session)
    today = _local_date(session)
    routine = Routine(
        title=title,
        priority=_validate_priority(payload.priority),
        weekdays=_weekdays_value(payload.weekdays),
        start_date=today,
    )
    session.add(routine)
    session.flush()
    _ensure_routine_occurrences(session, today)
    response = _routine_response(session, routine, today)
    session.commit()
    return response


def _routine_or_404(session: Session, routine_id: int) -> Routine:
    routine = session.get(Routine, routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="routine not found")
    return routine


@app.patch("/routines/{routine_id}", response_model=RoutineResponse)
def update_routine(
    routine_id: int,
    payload: RoutineUpdate,
    session: Session = Depends(get_session),
) -> RoutineResponse:
    _begin_immediate(session)
    routine = _routine_or_404(session, routine_id)
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            session.rollback()
            raise HTTPException(status_code=422, detail="title is required")
        routine.title = title
    if payload.priority is not None:
        routine.priority = _validate_priority(payload.priority)
    if payload.weekdays is not None:
        routine.weekdays = _weekdays_value(payload.weekdays)
    if payload.active is not None:
        routine.active = payload.active
    routine.updated_at = utcnow()
    session.add(routine)
    today = _local_date(session)
    current = session.exec(
        select(Todo).where(
            Todo.routine_id == routine.id,
            Todo.occurrence_date == today,
            Todo.status == STATUS_OPEN,
        )
    ).first()
    if current is not None:
        # Keep the visible, unfinished occurrence aligned with its template;
        # completed/skipped history remains immutable.
        current.title = routine.title
        current.priority = routine.priority
        current.updated_at = routine.updated_at
        session.add(current)
    _ensure_routine_occurrences(session, today)
    response = _routine_response(session, routine, today)
    session.commit()
    return response


@app.delete("/routines/{routine_id}", status_code=204)
def delete_routine(routine_id: int, session: Session = Depends(get_session)) -> Response:
    _begin_immediate(session)
    routine = _routine_or_404(session, routine_id)
    routine.active = False
    routine.updated_at = utcnow()
    session.add(routine)
    session.commit()
    return Response(status_code=204)


@app.get("/metrics")
def metrics(session: Session = Depends(get_session)) -> dict:
    _begin_immediate(session)
    today = _local_date(session)
    open_today = _open_todos(session)
    state, task_xp, lifetime_xp = _reconcile_arcade(session)
    events = session.exec(
        select(ProductivityEvent).where(ProductivityEvent.local_date == today)
    ).all()
    spins = _spins_today(session, today)
    response = {
        "timezone": _settings(session).timezone,
        "local_date": today.isoformat(),
        "lifetime_xp": lifetime_xp,
        "current_task_xp": task_xp,
        "daily_score": sum(event.daily_score_delta for event in events),
        "completed_today": sum(
            1
            for event in events
            if event.event_type in {
                "completion",
                "daily_completion",
                "legacy_completion",
            }
        ),
        "completion_streak": _completion_streak(session, today),
        "postponed_today": sum(1 for event in events if event.event_type == "postpone"),
        "open_today": len(open_today),
        "tickets": state.tickets,
        "chips": _chips_balance(session),
        "reward_spins_today": spins,
        "reward_spins_remaining": max(0, MAX_DAILY_REWARD_SPINS - spins),
        "max_daily_reward_spins": MAX_DAILY_REWARD_SPINS,
    }
    session.commit()
    return response


@app.get("/arcade", response_model=ArcadeStatusResponse)
def arcade_status(session: Session = Depends(get_session)) -> ArcadeStatusResponse:
    _begin_immediate(session)
    day = _local_date(session)
    state, task_xp, lifetime_xp = _reconcile_arcade(session)
    response = _arcade_status(session, state, task_xp, lifetime_xp, day)
    session.commit()
    return response


@app.post("/arcade/spin", response_model=ArcadeSpinResponse)
def spin_arcade(
    payload: Optional[OperationRequest] = None,
    session: Session = Depends(get_session),
) -> ArcadeSpinResponse:
    """Spend one ticket; every accepted server result wins Reward Chips."""
    _begin_immediate(session)
    operation_id = (payload.operation_id.strip() if payload and payload.operation_id else str(uuid4()))
    if not operation_id:
        session.rollback()
        raise HTTPException(status_code=422, detail="operation_id cannot be blank")
    existing = session.exec(
        select(ArcadeSpin).where(ArcadeSpin.operation_id == operation_id)
    ).first()
    if existing is not None:
        response = _spin_response(session, existing, idempotent=True)
        session.commit()
        return response

    today = _local_date(session)
    state, _, _ = _reconcile_arcade(session)
    spins = _spins_today(session, today)
    if spins >= MAX_DAILY_REWARD_SPINS:
        session.commit()
        raise HTTPException(
            status_code=429,
            detail="daily reward spin limit reached; ticket retained",
        )
    if state.tickets < 1:
        session.commit()
        raise HTTPException(status_code=409, detail="no Lucky Tickets available")

    outcome = arcade_rng.choices(
        ARCADE_OUTCOMES,
        weights=[entry["weight"] for entry in ARCADE_OUTCOMES],
        k=1,
    )[0]
    spun_at = utcnow()
    if outcome["outcome"] == "jackpot":
        quote_text, quote_attribution = quote_rng.choice(MOTIVATIONAL_QUOTES)
    else:
        quote_text, quote_attribution = "", None
    state.tickets -= 1
    state.spin_count += 1
    state.last_outcome = outcome["outcome"]
    state.last_symbols = json.dumps(outcome["symbols"], ensure_ascii=False)
    state.last_label = outcome["label"]
    state.last_message = outcome["message"]
    state.last_tier = outcome["tier"]
    state.last_spun_at = spun_at
    state.updated_at = spun_at
    session.add(state)
    spin = ArcadeSpin(
        operation_id=operation_id,
        outcome=outcome["outcome"],
        symbols_json=json.dumps(outcome["symbols"], ensure_ascii=False),
        label=outcome["label"],
        message=outcome["message"],
        tier=outcome["tier"],
        chips_awarded=outcome["chips"],
        spin_count=state.spin_count,
        tickets_remaining=state.tickets,
        quote_text=quote_text,
        quote_attribution=quote_attribution,
        local_date=today,
        spun_at=spun_at,
    )
    session.add(spin)
    session.flush()
    balance = _chips_balance(session)
    transaction = RewardTransaction(
        event_key=f"slot:{operation_id}",
        transaction_type="slot_award",
        amount=outcome["chips"],
        balance_after=balance + outcome["chips"],
        arcade_spin_id=spin.id,
        created_at=spun_at,
    )
    session.add(transaction)
    session.flush()
    response = _spin_response(session, spin, idempotent=False)
    session.commit()
    return response


def _reward_goal_or_404(session: Session, goal_id: int) -> RewardGoal:
    goal = session.get(RewardGoal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="reward goal not found")
    return goal


def _reward_history(session: Session, limit: int) -> list[RewardTransactionResponse]:
    transactions = session.exec(
        select(RewardTransaction)
        .order_by(RewardTransaction.id.desc())
        .limit(limit)
    ).all()
    return [_transaction_response(session, transaction) for transaction in transactions]


@app.get("/rewards", response_model=RewardOverviewResponse)
def rewards_overview(session: Session = Depends(get_session)) -> RewardOverviewResponse:
    balance = _chips_balance(session)
    goals = session.exec(
        select(RewardGoal).where(RewardGoal.active == True).order_by(RewardGoal.id)  # noqa: E712
    ).all()
    return RewardOverviewResponse(
        chips=balance,
        lifetime_chips_earned=_lifetime_chips(session),
        goals=[_goal_response(session, goal, balance) for goal in goals],
        recent_transactions=_reward_history(session, 20),
    )


@app.get("/rewards/history", response_model=list[RewardTransactionResponse])
def reward_history(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[RewardTransactionResponse]:
    return _reward_history(session, limit)


@app.post("/rewards/goals", status_code=201, response_model=RewardGoalResponse)
def create_reward_goal(
    payload: RewardGoalCreate, session: Session = Depends(get_session)
) -> RewardGoalResponse:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    if payload.cost_chips < 1:
        raise HTTPException(status_code=422, detail="cost_chips must be positive")
    _begin_immediate(session)
    goal = RewardGoal(title=title, cost_chips=payload.cost_chips)
    session.add(goal)
    session.flush()
    response = _goal_response(session, goal, _chips_balance(session))
    session.commit()
    return response


@app.patch("/rewards/goals/{goal_id}", response_model=RewardGoalResponse)
def update_reward_goal(
    goal_id: int,
    payload: RewardGoalUpdate,
    session: Session = Depends(get_session),
) -> RewardGoalResponse:
    _begin_immediate(session)
    goal = _reward_goal_or_404(session, goal_id)
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            session.rollback()
            raise HTTPException(status_code=422, detail="title is required")
        goal.title = title
    if payload.cost_chips is not None:
        if payload.cost_chips < 1:
            session.rollback()
            raise HTTPException(status_code=422, detail="cost_chips must be positive")
        goal.cost_chips = payload.cost_chips
    if payload.active is not None:
        goal.active = payload.active
    goal.updated_at = utcnow()
    session.add(goal)
    session.flush()
    response = _goal_response(session, goal, _chips_balance(session))
    session.commit()
    return response


@app.delete("/rewards/goals/{goal_id}", status_code=204)
def delete_reward_goal(goal_id: int, session: Session = Depends(get_session)) -> Response:
    _begin_immediate(session)
    goal = _reward_goal_or_404(session, goal_id)
    goal.active = False
    goal.updated_at = utcnow()
    session.add(goal)
    session.commit()
    return Response(status_code=204)


@app.post("/rewards/goals/{goal_id}/redeem", response_model=RewardRedeemResponse)
def redeem_reward_goal(
    goal_id: int,
    payload: Optional[OperationRequest] = None,
    session: Session = Depends(get_session),
) -> RewardRedeemResponse:
    _begin_immediate(session)
    goal = _reward_goal_or_404(session, goal_id)
    operation_id = (payload.operation_id.strip() if payload and payload.operation_id else str(uuid4()))
    if not operation_id:
        session.rollback()
        raise HTTPException(status_code=422, detail="operation_id cannot be blank")
    key = f"redeem:{operation_id}"
    existing = session.exec(
        select(RewardTransaction).where(RewardTransaction.event_key == key)
    ).first()
    if existing is not None:
        if existing.goal_id != goal_id:
            session.rollback()
            raise HTTPException(status_code=409, detail="operation_id was used for another reward")
        balance = _chips_balance(session)
        response = RewardRedeemResponse(
            goal=_goal_response(session, goal, balance),
            transaction=_transaction_response(session, existing),
            chips_balance=balance,
            idempotent=True,
        )
        session.commit()
        return response
    if not goal.active:
        session.rollback()
        raise HTTPException(status_code=404, detail="reward goal not found")
    balance = _chips_balance(session)
    if balance < goal.cost_chips:
        session.rollback()
        raise HTTPException(status_code=409, detail="not enough Reward Chips")
    transaction = RewardTransaction(
        event_key=key,
        transaction_type="redeem",
        amount=-goal.cost_chips,
        balance_after=balance - goal.cost_chips,
        goal_id=goal.id,
        goal_title=goal.title,
        created_at=utcnow(),
    )
    session.add(transaction)
    session.flush()
    new_balance = balance - goal.cost_chips
    response = RewardRedeemResponse(
        goal=_goal_response(session, goal, new_balance),
        transaction=_transaction_response(session, transaction),
        chips_balance=new_balance,
        idempotent=False,
    )
    session.commit()
    return response


@app.get("/wheel/spin")
def spin(session: Session = Depends(get_session)) -> Optional[Todo]:
    """Spin only among currently actionable tasks."""
    _begin_immediate(session)
    now = utcnow()
    chosen = wheel.pick_weighted(_open_todos(session, now=now), now=now)
    session.commit()
    if chosen is not None:
        session.refresh(chosen)
    return chosen


if __name__ == "__main__":
    import threading
    import webbrowser

    import uvicorn

    host = os.environ.get("TYCHE_HOST", "127.0.0.1")
    port = int(os.environ.get("TYCHE_PORT", "8000"))
    url = f"http://{host}:{port}"
    if os.environ.get("TYCHE_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"To-Do Gambling running at {url}  (Ctrl+C to stop)")
    uvicorn.run(app, host=host, port=port)
