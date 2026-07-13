"""TYCHE — FastAPI app.

Add tasks, manage them in a list (edit / delete / priority), and spin the wheel
for an age-weighted random open task.

Run standalone with ``python main.py`` (boots uvicorn and opens the browser) or
via ``uvicorn main:app --reload``.
"""

from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select

import wheel
from database import get_session, init_db
from models import STATUS_DONE, STATUS_OPEN, Todo, utcnow

PRIORITY_MIN, PRIORITY_MAX = 1, 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="TYCHE", lifespan=lifespan)

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
    session.commit()
    session.refresh(todo)
    return todo


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int, session: Session = Depends(get_session)) -> Response:
    """Hard-delete a todo."""
    todo = _get_or_404(session, todo_id)
    session.delete(todo)
    session.commit()
    return Response(status_code=204)


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
    print(f"TYCHE running at {url}  (Ctrl+C to stop)")
    uvicorn.run(app, host=host, port=port)
