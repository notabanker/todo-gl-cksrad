"""API tests for the create / list / spin endpoints.

Uses an isolated in-memory SQLite DB via a dependency override so tests never
touch the real tyche.db file.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import main
from database import get_session
from models import ArcadeState, Todo, utcnow


@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[get_session] = session_override
    with TestClient(main.app) as client:
        client.engine = engine  # expose for tests that need direct seeding
        yield client
    main.app.dependency_overrides.clear()


def _add_todo(engine, title, *, status="open", age_days=1, priority=2):
    created = utcnow() - timedelta(days=age_days)
    with Session(engine) as session:
        todo = Todo(
            title=title, status=status, priority=priority,
            created_at=created, updated_at=created,
        )
        session.add(todo)
        session.commit()
        session.refresh(todo)
        return todo.id


def test_create_todo_returns_json_and_persists(client):
    res = client.post("/todos", json={"title": "Buy milk", "priority": 1})
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "Buy milk"
    assert body["status"] == "open"
    assert body["priority"] == 1

    listed = client.get("/todos").json()
    assert [t["title"] for t in listed] == ["Buy milk"]


def test_create_todo_defaults_priority_to_2(client):
    body = client.post("/todos", json={"title": "no prio"}).json()
    assert body["priority"] == 2


def test_create_todo_requires_title(client):
    assert client.post("/todos", json={}).status_code == 422  # missing
    assert client.post("/todos", json={"title": "   "}).status_code == 422  # blank


def test_create_todo_rejects_bad_priority(client):
    assert client.post("/todos", json={"title": "x", "priority": 5}).status_code == 422


def test_update_todo_changes_title_and_priority(client):
    todo_id = _add_todo(client.engine, "old title", priority=3)
    res = client.patch(f"/todos/{todo_id}", json={"title": "new title", "priority": 1})
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "new title"
    assert body["priority"] == 1


def test_complete_and_reopen_todo(client):
    todo_id = _add_todo(client.engine, "finish me")

    completed = client.patch(f"/todos/{todo_id}", json={"status": "done"})
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"
    assert completed.json()["completed_at"] is not None
    assert client.get("/todos").json() == []
    assert [todo["id"] for todo in client.get("/todos?status=done").json()] == [todo_id]

    reopened = client.patch(f"/todos/{todo_id}", json={"status": "open"})
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    assert reopened.json()["completed_at"] is None
    assert [todo["id"] for todo in client.get("/todos").json()] == [todo_id]


def test_update_rejects_invalid_status(client):
    todo_id = _add_todo(client.engine, "status test")
    assert client.patch(f"/todos/{todo_id}", json={"status": "later"}).status_code == 422


def test_done_list_orders_most_recent_completion_first(client):
    first_id = _add_todo(client.engine, "first")
    second_id = _add_todo(client.engine, "second")
    client.patch(f"/todos/{first_id}", json={"status": "done"})
    client.patch(f"/todos/{second_id}", json={"status": "done"})

    assert [todo["id"] for todo in client.get("/todos?status=done").json()] == [
        second_id,
        first_id,
    ]


def test_update_missing_todo_is_404(client):
    assert client.patch("/todos/999", json={"title": "x"}).status_code == 404


def test_delete_todo_removes_it(client):
    todo_id = _add_todo(client.engine, "delete me")
    assert client.delete(f"/todos/{todo_id}").status_code == 204
    assert client.get("/todos").json() == []


def test_delete_missing_todo_is_404(client):
    assert client.delete("/todos/999").status_code == 404


def test_list_orders_by_priority_then_age(client):
    _add_todo(client.engine, "low prio old", priority=3, age_days=30)
    _add_todo(client.engine, "high prio new", priority=1, age_days=1)
    titles = [t["title"] for t in client.get("/todos").json()]
    assert titles == ["high prio new", "low prio old"]


def test_list_returns_only_open(client):
    _add_todo(client.engine, "open one", status="open")
    _add_todo(client.engine, "done one", status="done")

    titles = [t["title"] for t in client.get("/todos").json()]
    assert titles == ["open one"]


def test_spin_returns_null_when_empty(client):
    assert client.get("/wheel/spin").json() is None


def test_spin_returns_an_open_todo(client):
    _add_todo(client.engine, "old", age_days=30)
    _add_todo(client.engine, "new", age_days=1)

    chosen = client.get("/wheel/spin").json()
    assert chosen["title"] in {"old", "new"}
    assert chosen["status"] == "open"


def test_spin_excludes_completed_todos(client):
    completed_id = _add_todo(client.engine, "already done", age_days=100)
    _add_todo(client.engine, "still open", age_days=1)
    client.patch(f"/todos/{completed_id}", json={"status": "done"})

    chosen = client.get("/wheel/spin").json()
    assert chosen["title"] == "still open"


def test_arcade_mints_one_ticket_for_each_new_100_xp_threshold(client):
    for index in range(4):
        _add_todo(
            client.engine,
            f"completed P1 #{index}",
            status="done",
            priority=1,
            age_days=0,
        )

    response = client.get("/arcade")
    assert response.status_code == 200
    assert response.json() == {
        "tickets": 2,
        "task_xp": 200,
        "xp_to_next_ticket": 100,
        "last_spin": None,
    }

    with Session(client.engine) as session:
        state = session.get(ArcadeState, 1)
        assert state.highest_xp_threshold == 200
        assert state.tickets == 2


def test_arcade_task_xp_matches_priority_and_waiting_bonus_formula(client):
    # P2 base 30 + capped 30 waiting bonus; P3 base 20 + 4 waiting XP.
    completed = utcnow()
    with Session(client.engine) as session:
        session.add(
            Todo(
                title="old P2",
                status="done",
                priority=2,
                created_at=completed - timedelta(days=20),
                updated_at=completed,
                completed_at=completed,
            )
        )
        session.add(
            Todo(
                title="two-day P3",
                status="done",
                priority=3,
                created_at=completed - timedelta(days=2),
                updated_at=completed,
                completed_at=completed,
            )
        )
        session.commit()

    assert client.get("/arcade").json() == {
        "tickets": 0,
        "task_xp": 84,
        "xp_to_next_ticket": 16,
        "last_spin": None,
    }


def test_arcade_does_not_remint_after_reopen_delete_or_recomplete(client):
    first = client.post("/todos", json={"title": "first", "priority": 1}).json()
    second = client.post("/todos", json={"title": "second", "priority": 1}).json()
    assert client.patch(f"/todos/{first['id']}", json={"status": "done"}).status_code == 200
    assert client.patch(f"/todos/{second['id']}", json={"status": "done"}).status_code == 200
    assert client.get("/arcade").json()["tickets"] == 1

    # Dropping below the boundary and returning to it must not mint it again.
    client.patch(f"/todos/{first['id']}", json={"status": "open"})
    assert client.get("/arcade").json()["task_xp"] == 50
    client.patch(f"/todos/{first['id']}", json={"status": "done"})
    assert client.get("/arcade").json()["tickets"] == 1

    # Deletion is another XP drop; replacement XP at the old threshold is not
    # a new lifetime high-water boundary either.
    client.delete(f"/todos/{first['id']}")
    third = client.post("/todos", json={"title": "third", "priority": 1}).json()
    client.patch(f"/todos/{third['id']}", json={"status": "done"})
    status = client.get("/arcade").json()
    assert status["task_xp"] == 100
    assert status["tickets"] == 1

    with Session(client.engine) as session:
        assert session.get(ArcadeState, 1).highest_xp_threshold == 100


def test_arcade_spin_is_server_result_and_consumes_one_ticket(client, monkeypatch):
    _add_todo(
        client.engine, "completed one", status="done", priority=1, age_days=0
    )
    _add_todo(
        client.engine, "completed two", status="done", priority=1, age_days=0
    )

    class JackpotRng:
        def choices(self, population, *, weights, k):
            assert weights == [50, 30, 15, 4, 1]
            assert k == 1
            return [next(item for item in population if item["outcome"] == "jackpot")]

    monkeypatch.setattr(main, "arcade_rng", JackpotRng())
    response = client.post("/arcade/spin")
    assert response.status_code == 200
    body = response.json()
    assert body["symbols"] == ["7️⃣", "7️⃣", "7️⃣"]
    assert body["outcome"] == "jackpot"
    assert body["label"] == "Focus Jackpot"
    assert body["tier"] == "jackpot"
    assert body["message"]
    assert body["spin_count"] == 1
    assert body["remaining_tickets"] == 0
    assert body["spun_at"]

    # A spin never changes productivity XP and its exact server result persists.
    status = client.get("/arcade").json()
    assert status["tickets"] == 0
    assert status["task_xp"] == 100
    assert status["last_spin"] == {
        key: body[key]
        for key in (
            "symbols",
            "outcome",
            "label",
            "message",
            "tier",
            "spin_count",
            "spun_at",
        )
    }

    out_of_tickets = client.post("/arcade/spin")
    assert out_of_tickets.status_code == 409
    assert out_of_tickets.json() == {"detail": "no Lucky Tickets available"}

    with Session(client.engine) as session:
        state = session.get(ArcadeState, 1)
        assert state.tickets == 0
        assert state.spin_count == 1


def test_arcade_spin_requires_a_ticket(client):
    response = client.post("/arcade/spin")
    assert response.status_code == 409
    assert response.json() == {"detail": "no Lucky Tickets available"}
