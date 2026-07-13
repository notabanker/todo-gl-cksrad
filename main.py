"""To-Do Gambling — FastAPI app.

Add tasks, manage them in a list (edit / delete / priority), and spin the wheel
for an age-weighted random open task.

Run standalone with ``python main.py`` (boots uvicorn and opens the browser) or
via ``uvicorn main:app --reload``.
"""

from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select

import wheel
from database import get_session, init_db
from models import ArcadeState, STATUS_DONE, STATUS_OPEN, Todo, utcnow

PRIORITY_MIN, PRIORITY_MAX = 1, 3
XP_PER_TICKET = 100

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
    },
    {
        "outcome": "focus_15",
        "weight": 30,
        "symbols": ["🎯", "⏱️", "🎯"],
        "label": "15-Minute Focus",
        "message": "Choose one task and give it 15 distraction-free minutes.",
        "tier": "common",
    },
    {
        "outcome": "focus_25",
        "weight": 15,
        "symbols": ["🔥", "🧠", "🔥"],
        "label": "25-Minute Power Block",
        "message": "Take your most important task through one 25-minute focus block.",
        "tier": "uncommon",
    },
    {
        "outcome": "break_5",
        "weight": 4,
        "symbols": ["☕", "🌿", "☕"],
        "label": "5-Minute Reset",
        "message": "Take five intentional minutes, then return refreshed to one task.",
        "tier": "rare",
    },
    {
        "outcome": "jackpot",
        "weight": 1,
        "symbols": ["7️⃣", "7️⃣", "7️⃣"],
        "label": "Focus Jackpot",
        "message": "Clear the runway and give your top task one heroic focus block.",
        "tier": "jackpot",
    },
)
arcade_rng = secrets.SystemRandom()


@asynccontextmanager
async def lifespan(app: FastAPI):
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


class ArcadeSpinResult(BaseModel):
    symbols: list[str]
    outcome: str
    label: str
    message: str
    tier: str
    spin_count: int
    spun_at: datetime


class ArcadeStatusResponse(BaseModel):
    tickets: int
    task_xp: int
    xp_to_next_ticket: int
    last_spin: Optional[ArcadeSpinResult]


class ArcadeSpinResponse(ArcadeSpinResult):
    remaining_tickets: int


# --- helpers -----------------------------------------------------------------

def _open_todos(session: Session) -> list[Todo]:
    """Open todos ordered by priority (high first), then oldest first."""
    return list(
        session.exec(
            select(Todo)
            .where(Todo.status == STATUS_OPEN)
            .order_by(Todo.priority, Todo.created_at)
        )
    )


def _done_todos(session: Session) -> list[Todo]:
    """Completed todos ordered by most recent completion first."""
    return list(
        session.exec(
            select(Todo)
            .where(Todo.status == STATUS_DONE)
            .order_by(Todo.completed_at.desc(), Todo.updated_at.desc(), Todo.id.desc())
        )
    )


def _validate_priority(priority: int) -> int:
    if not PRIORITY_MIN <= priority <= PRIORITY_MAX:
        raise HTTPException(status_code=422, detail="priority must be 1, 2, or 3")
    return priority


def _get_or_404(session: Session, todo_id: int) -> Todo:
    todo = session.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="todo not found")
    return todo


def _task_points(todo: Todo) -> int:
    """Mirror the browser's productivity XP formula for one completed task."""
    priority_base = {1: 50, 2: 30, 3: 20}.get(todo.priority, 20)
    completed = todo.completed_at or todo.updated_at
    waited_days = max(0, int((completed - todo.created_at).total_seconds() // 86400))
    return priority_base + min(30, waited_days * 2)


def _task_xp(session: Session) -> int:
    """Current productivity XP, derived only from completed Todo rows."""
    session.flush()
    completed = session.exec(select(Todo).where(Todo.status == STATUS_DONE))
    return sum(_task_points(todo) for todo in completed)


def _arcade_state(session: Session) -> ArcadeState:
    state = session.get(ArcadeState, 1)
    if state is None:
        state = ArcadeState(id=1)
        session.add(state)
        session.flush()
    return state


def _reconcile_arcade(session: Session) -> tuple[ArcadeState, int]:
    """Mint tickets for newly crossed 100-XP high-water boundaries."""
    state = _arcade_state(session)
    task_xp = _task_xp(session)
    reached_threshold = (task_xp // XP_PER_TICKET) * XP_PER_TICKET
    if reached_threshold > state.highest_xp_threshold:
        newly_crossed = (
            reached_threshold - state.highest_xp_threshold
        ) // XP_PER_TICKET
        state.tickets += newly_crossed
        state.highest_xp_threshold = reached_threshold
        state.updated_at = utcnow()
        session.add(state)
    return state, task_xp


def _last_spin(state: ArcadeState) -> Optional[ArcadeSpinResult]:
    if not all(
        (
            state.last_outcome,
            state.last_symbols,
            state.last_label,
            state.last_message,
            state.last_tier,
            state.last_spun_at,
        )
    ):
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


def _arcade_status(state: ArcadeState, task_xp: int) -> ArcadeStatusResponse:
    next_unminted_threshold = state.highest_xp_threshold + XP_PER_TICKET
    return ArcadeStatusResponse(
        tickets=state.tickets,
        task_xp=task_xp,
        xp_to_next_ticket=max(0, next_unminted_threshold - task_xp),
        last_spin=_last_spin(state),
    )


def _begin_immediate(session: Session) -> None:
    """Serialize arcade reconciliation and ticket spending on SQLite."""
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


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
    """JSON list of open todos, or completed todos when requested."""
    return _done_todos(session) if status == STATUS_DONE else _open_todos(session)


@app.post("/todos", status_code=201)
def create_todo(
    payload: TodoCreate, session: Session = Depends(get_session)
) -> Todo:
    """Create a todo from JSON. Returns the created todo."""
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    todo = Todo(
        title=title,
        priority=_validate_priority(payload.priority),
        status=STATUS_OPEN,
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
    todo = _get_or_404(session, todo_id)
    # Capture the old XP high-water before a completed task can be reopened or
    # have its priority reduced. This is what makes earned tickets permanent.
    if todo.status == STATUS_DONE:
        _reconcile_arcade(session)
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="title is required")
        todo.title = title
    if payload.priority is not None:
        todo.priority = _validate_priority(payload.priority)
    if payload.status is not None and payload.status != todo.status:
        todo.status = payload.status
        todo.completed_at = utcnow() if payload.status == STATUS_DONE else None
    todo.updated_at = utcnow()
    session.add(todo)
    # Completing a task (or increasing a completed task's priority) may cross
    # one or more new ticket thresholds in this same transaction.
    if todo.status == STATUS_DONE:
        _reconcile_arcade(session)
    session.commit()
    session.refresh(todo)
    return todo


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int, session: Session = Depends(get_session)) -> Response:
    """Hard-delete a todo."""
    todo = _get_or_404(session, todo_id)
    if todo.status == STATUS_DONE:
        _reconcile_arcade(session)
    session.delete(todo)
    session.commit()
    return Response(status_code=204)


@app.get("/arcade", response_model=ArcadeStatusResponse)
def arcade_status(session: Session = Depends(get_session)) -> ArcadeStatusResponse:
    """Return persistent Lucky Tickets and the current task-XP progress."""
    _begin_immediate(session)
    state, task_xp = _reconcile_arcade(session)
    response = _arcade_status(state, task_xp)
    session.commit()
    return response


@app.post("/arcade/spin", response_model=ArcadeSpinResponse)
def spin_arcade(session: Session = Depends(get_session)) -> ArcadeSpinResponse:
    """Spend one Lucky Ticket on a server-authoritative focus prompt."""
    _begin_immediate(session)
    state, _ = _reconcile_arcade(session)
    if state.tickets < 1:
        # Commit first so an initial singleton/high-water reconciliation is not
        # rolled back merely because there is not yet a ticket to spend.
        session.commit()
        raise HTTPException(status_code=409, detail="no Lucky Tickets available")

    outcome = arcade_rng.choices(
        ARCADE_OUTCOMES,
        weights=[entry["weight"] for entry in ARCADE_OUTCOMES],
        k=1,
    )[0]
    spun_at = utcnow()
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

    response = ArcadeSpinResponse(
        symbols=outcome["symbols"],
        outcome=outcome["outcome"],
        label=outcome["label"],
        message=outcome["message"],
        tier=outcome["tier"],
        spin_count=state.spin_count,
        spun_at=spun_at,
        remaining_tickets=state.tickets,
    )
    session.commit()
    return response


@app.get("/wheel/spin")
def spin(session: Session = Depends(get_session)) -> Optional[Todo]:
    """Spin the wheel: age-weighted random pick among open todos (or null)."""
    return wheel.pick_weighted(_open_todos(session), now=utcnow())


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
