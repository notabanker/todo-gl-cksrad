"""Regression coverage for the local-day, routine, and reward foundations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select
from sqlmodel.pool import StaticPool

import main
from database import get_session, init_db
from models import (
    AppSettings,
    ArcadeSpin,
    ArcadeState,
    ProductivityEvent,
    RewardGoal,
    RewardTransaction,
    Todo,
)


@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine, create_backup=False)
    with Session(engine) as session:
        settings = session.get(AppSettings, 1)
        settings.timezone = "Europe/Berlin"
        session.add(settings)
        session.commit()

    def session_override():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[get_session] = session_override
    with TestClient(main.app) as test_client:
        test_client.engine = engine
        yield test_client
    main.app.dependency_overrides.clear()


def _clock(monkeypatch, value: datetime) -> None:
    monkeypatch.setattr(main, "utcnow", lambda: value)


def _complete(client: TestClient, title: str, priority: int = 1) -> int:
    todo = client.post("/todos", json={"title": title, "priority": priority}).json()
    response = client.patch(f"/todos/{todo['id']}", json={"status": "done"})
    assert response.status_code == 200
    return todo["id"]


def test_postpone_is_one_time_hidden_and_idempotent_across_midnight(
    client, monkeypatch
):
    before_midnight = datetime(2026, 7, 13, 21, 50)  # 23:50 in Berlin
    _clock(monkeypatch, before_midnight)
    todo = client.post("/todos", json={"title": "Send form", "priority": 1}).json()

    first = client.post(
        f"/todos/{todo['id']}/postpone",
        json={"operation_id": "postpone-form"},
    )
    assert first.status_code == 200
    assert first.json()["todo"]["id"] == todo["id"]
    assert first.json()["postponed_to"] == "2026-07-13T22:00:00"
    assert first.json()["daily_score_delta"] == -10
    assert first.json()["daily_score"] == -10
    assert first.json()["idempotent"] is False
    assert client.get("/today").json() == []
    assert client.get("/wheel/spin").json() is None
    assert [item["id"] for item in client.get("/tomorrow").json()] == [todo["id"]]

    # The exact same operation remains idempotent after the local date rolls.
    _clock(monkeypatch, datetime(2026, 7, 13, 22, 5))  # 00:05 next day
    replay = client.post(
        f"/todos/{todo['id']}/postpone",
        json={"operation_id": "postpone-form"},
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["local_date"] == "2026-07-13"
    assert replay.json()["daily_score"] == -10
    assert [item["id"] for item in client.get("/today").json()] == [todo["id"]]

    with Session(client.engine) as session:
        events = session.exec(
            select(ProductivityEvent).where(
                ProductivityEvent.event_type == "postpone"
            )
        ).all()
        assert len(events) == 1
        assert events[0].daily_score_delta == -10


def test_postpone_never_reduces_lifetime_xp(client, monkeypatch):
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    todo_id = _complete(client, "Already earned", priority=1)
    assert client.get("/metrics").json()["lifetime_xp"] == 50
    assert client.patch(f"/todos/{todo_id}", json={"status": "open"}).status_code == 200
    assert client.post(f"/todos/{todo_id}/postpone").status_code == 200
    metrics = client.get("/metrics").json()
    assert metrics["lifetime_xp"] == 50
    assert metrics["daily_score"] == 40


@pytest.mark.parametrize(
    ("postpone_days", "expected_xp"),
    ((1, 50), (2, 52)),
)
def test_postponed_hours_do_not_inflate_waiting_bonus(
    client, monkeypatch, postpone_days, expected_xp
):
    start = datetime(2026, 7, 13, 10, 0)  # local noon
    _clock(monkeypatch, start)
    todo = client.post(
        "/todos", json={"title": f"Deferred {postpone_days}", "priority": 1}
    ).json()
    # Pin creation to the controlled clock; model defaults use the real clock.
    with Session(client.engine) as session:
        stored = session.get(Todo, todo["id"])
        stored.created_at = start
        stored.updated_at = start
        session.add(stored)
        session.commit()

    for offset in range(postpone_days):
        _clock(monkeypatch, start + timedelta(days=offset))
        postponed = client.post(
            f"/todos/{todo['id']}/postpone",
            json={"operation_id": f"defer-{postpone_days}-{offset}"},
        )
        assert postponed.status_code == 200

    _clock(monkeypatch, start + timedelta(days=postpone_days))
    assert client.patch(f"/todos/{todo['id']}", json={"status": "done"}).status_code == 200
    assert client.get("/metrics").json()["lifetime_xp"] == expected_xp
    with Session(client.engine) as session:
        stored = session.get(Todo, todo["id"])
        assert stored.unavailable_seconds == postpone_days * 12 * 60 * 60


def test_routines_generate_once_score_streaks_and_break_on_skip(client, monkeypatch):
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))  # Monday
    created = client.post(
        "/routines",
        json={"title": "Plan tomorrow", "priority": 2, "weekdays": list(range(7))},
    )
    assert created.status_code == 201
    routine_id = created.json()["id"]

    today = [item for item in client.get("/today").json() if item["routine_id"] == routine_id]
    assert len(today) == 1
    today_id = today[0]["id"]
    assert client.get("/today").json()[0]["id"] == today_id

    # Materialized future entries stay in sync with their template.
    tomorrow_before = client.get("/tomorrow").json()
    tomorrow_id = next(item["id"] for item in tomorrow_before if item["routine_id"] == routine_id)
    updated = client.patch(
        f"/routines/{routine_id}", json={"title": "Plan and review"}
    )
    assert updated.status_code == 200
    assert next(
        item for item in client.get("/today").json() if item["id"] == today_id
    )["title"] == "Plan and review"
    synced_tomorrow = next(
        item for item in client.get("/tomorrow").json() if item["id"] == tomorrow_id
    )
    assert synced_tomorrow["title"] == "Plan and review"

    # Daily XP is priority base + current streak day, never waiting-age XP.
    assert client.patch(f"/todos/{today_id}", json={"status": "done"}).status_code == 200
    assert client.get("/metrics").json()["lifetime_xp"] == 31
    assert client.patch(f"/todos/{today_id}", json={"status": "open"}).status_code == 409
    assert client.delete(f"/todos/{today_id}").status_code == 409

    _clock(monkeypatch, datetime(2026, 7, 14, 10, 0))
    second = next(
        item for item in client.get("/today").json() if item["routine_id"] == routine_id
    )
    assert second["id"] == tomorrow_id
    assert client.patch(f"/todos/{second['id']}", json={"status": "done"}).status_code == 200
    assert client.get("/metrics").json()["lifetime_xp"] == 63  # 31 + 32

    _clock(monkeypatch, datetime(2026, 7, 15, 10, 0))
    third = next(
        item for item in client.get("/today").json() if item["routine_id"] == routine_id
    )
    skipped = client.post(
        f"/todos/{third['id']}/postpone", json={"operation_id": "skip-daily"}
    )
    assert skipped.status_code == 200
    assert skipped.json()["todo"]["status"] == "skipped"
    assert client.patch(f"/todos/{third['id']}", json={"status": "open"}).status_code == 409
    assert not any(item["id"] == third["id"] for item in client.get("/today").json())
    tomorrow_once = [
        item for item in client.get("/tomorrow").json() if item["routine_id"] == routine_id
    ]
    tomorrow_twice = [
        item for item in client.get("/tomorrow").json() if item["routine_id"] == routine_id
    ]
    assert len(tomorrow_once) == 1
    assert [item["id"] for item in tomorrow_once] == [item["id"] for item in tomorrow_twice]

    _clock(monkeypatch, datetime(2026, 7, 16, 10, 0))
    fourth = next(
        item for item in client.get("/today").json() if item["routine_id"] == routine_id
    )
    assert client.patch(f"/todos/{fourth['id']}", json={"status": "done"}).status_code == 200
    metrics = client.get("/metrics").json()
    assert metrics["lifetime_xp"] == 94  # skip breaks streak; next completion is +1
    assert metrics["daily_score"] == 31
    routine = next(item for item in client.get("/routines").json() if item["id"] == routine_id)
    assert routine["current_streak"] == 1


@pytest.mark.parametrize(
    "occurrence_patch",
    (
        {"title": "Edited on the occurrence"},
        {"priority": 1},
        {"title": "Edited and completed", "priority": 1, "status": "done"},
    ),
)
def test_daily_occurrence_edits_must_go_through_routine(
    client, monkeypatch, occurrence_patch
):
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    routine = client.post(
        "/routines",
        json={"title": "Morning plan", "priority": 2, "weekdays": list(range(7))},
    ).json()
    occurrence = next(
        item
        for item in client.get("/today").json()
        if item["routine_id"] == routine["id"]
    )

    rejected = client.patch(f"/todos/{occurrence['id']}", json=occurrence_patch)

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == (
        "daily occurrence titles and priorities are managed by their routine; "
        f"update PATCH /routines/{routine['id']} instead"
    )
    stored = next(
        item
        for item in client.get("/today").json()
        if item["id"] == occurrence["id"]
    )
    assert stored["title"] == "Morning plan"
    assert stored["priority"] == 2
    assert stored["status"] == "open"


def test_daily_status_only_patch_and_one_off_edits_remain_supported(client, monkeypatch):
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    routine = client.post(
        "/routines",
        json={"title": "Daily focus", "priority": 2, "weekdays": list(range(7))},
    ).json()
    occurrence = next(
        item
        for item in client.get("/today").json()
        if item["routine_id"] == routine["id"]
    )

    completed = client.patch(f"/todos/{occurrence['id']}", json={"status": "done"})
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"

    one_off = client.post(
        "/todos", json={"title": "Original one-off", "priority": 3}
    ).json()
    edited = client.patch(
        f"/todos/{one_off['id']}",
        json={"title": "Edited one-off", "priority": 1},
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "Edited one-off"
    assert edited.json()["priority"] == 1


def test_inactive_or_unscheduled_routine_occurrences_are_not_actionable(
    client, monkeypatch
):
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    routine = client.post(
        "/routines",
        json={"title": "Visible daily", "priority": 2, "weekdays": list(range(7))},
    ).json()
    today_id = next(
        item["id"] for item in client.get("/today").json() if item["routine_id"] == routine["id"]
    )
    tomorrow_id = next(
        item["id"] for item in client.get("/tomorrow").json() if item["routine_id"] == routine["id"]
    )

    assert client.patch(f"/routines/{routine['id']}", json={"active": False}).status_code == 200
    assert all(item["routine_id"] != routine["id"] for item in client.get("/today").json())
    assert all(item["routine_id"] != routine["id"] for item in client.get("/tomorrow").json())
    assert client.patch(f"/todos/{today_id}", json={"status": "done"}).status_code == 409
    assert client.post(f"/todos/{today_id}/postpone").status_code == 409

    # Reactivation reuses, rather than duplicates, both immutable occurrences.
    assert client.patch(f"/routines/{routine['id']}", json={"active": True}).status_code == 200
    assert next(
        item["id"] for item in client.get("/today").json() if item["routine_id"] == routine["id"]
    ) == today_id
    assert next(
        item["id"] for item in client.get("/tomorrow").json() if item["routine_id"] == routine["id"]
    ) == tomorrow_id


def test_metrics_streak_comes_from_ledger_even_after_done_tasks_are_deleted(
    client, monkeypatch
):
    _clock(monkeypatch, datetime(2026, 7, 11, 10, 0))
    first = _complete(client, "Day one")
    assert client.delete(f"/todos/{first}").status_code == 204

    _clock(monkeypatch, datetime(2026, 7, 12, 10, 0))
    second = _complete(client, "Day two")
    assert client.delete(f"/todos/{second}").status_code == 204

    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    metrics = client.get("/metrics").json()
    assert metrics["completion_streak"] == 2
    assert metrics["completed_today"] == 0
    assert metrics["lifetime_xp"] == 100

    _clock(monkeypatch, datetime(2026, 7, 14, 10, 0))
    assert client.get("/metrics").json()["completion_streak"] == 0


class _FixedOutcomeRng:
    def __init__(self, outcome: str):
        self.outcome = outcome

    def choices(self, population, *, weights, k):
        assert weights == [50, 30, 15, 4, 1]
        assert k == 1
        return [next(item for item in population if item["outcome"] == self.outcome)]


class _FixedQuoteRng:
    def choice(self, population):
        return population[0]


def test_slot_cap_idempotency_chip_savings_and_reward_redemption(
    client, monkeypatch
):
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    for index in range(8):
        _complete(client, f"Ticket XP {index}", priority=1)
    assert client.get("/arcade").json()["tickets"] == 4

    monkeypatch.setattr(main, "arcade_rng", _FixedOutcomeRng("quick_win"))
    spins = []
    for index in range(3):
        response = client.post(
            "/arcade/spin", json={"operation_id": f"spin-{index}"}
        )
        assert response.status_code == 200
        assert response.json()["chips_awarded"] == 5
        assert response.json()["quote"] is None
        spins.append(response.json())
    assert spins[-1]["chips_balance"] == 15
    assert spins[-1]["remaining_tickets"] == 1
    assert spins[-1]["reward_spins_remaining"] == 0

    fourth = client.post("/arcade/spin", json={"operation_id": "spin-four"})
    assert fourth.status_code == 429
    status = client.get("/arcade").json()
    assert status["tickets"] == 1  # rejected spin retains its ticket
    assert status["chips"] == 15

    # Replaying an older result returns its exact outcome and current balances.
    replay = client.post("/arcade/spin", json={"operation_id": "spin-0"})
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["spun_at"] == spins[0]["spun_at"]
    assert replay.json()["chips_balance"] == 15
    assert replay.json()["remaining_tickets"] == 1
    assert replay.json()["reward_spins_remaining"] == 0

    goal = client.post(
        "/rewards/goals", json={"title": "Fancy coffee", "cost_chips": 10}
    ).json()
    redeemed = client.post(
        f"/rewards/goals/{goal['id']}/redeem",
        json={"operation_id": "redeem-coffee"},
    )
    assert redeemed.status_code == 200
    assert redeemed.json()["transaction"]["amount"] == -10
    assert redeemed.json()["transaction"]["balance_after"] == 5
    assert redeemed.json()["transaction"]["goal_title"] == "Fancy coffee"
    assert redeemed.json()["chips_balance"] == 5
    assert redeemed.json()["idempotent"] is False
    replay_redeem = client.post(
        f"/rewards/goals/{goal['id']}/redeem",
        json={"operation_id": "redeem-coffee"},
    )
    assert replay_redeem.status_code == 200
    assert replay_redeem.json()["idempotent"] is True
    assert replay_redeem.json()["transaction"]["id"] == redeemed.json()["transaction"]["id"]

    assert client.patch(
        f"/rewards/goals/{goal['id']}", json={"title": "Renamed reward"}
    ).status_code == 200
    history = client.get("/rewards/history").json()
    redemption = next(item for item in history if item["transaction_type"] == "redeem")
    assert redemption["goal_title"] == "Fancy coffee"
    other = client.post(
        "/rewards/goals", json={"title": "Other", "cost_chips": 5}
    ).json()
    assert client.post(
        f"/rewards/goals/{other['id']}/redeem",
        json={"operation_id": "redeem-coffee"},
    ).status_code == 409

    with Session(client.engine) as session:
        redeems = session.exec(
            select(RewardTransaction).where(
                RewardTransaction.transaction_type == "redeem"
            )
        ).all()
        assert len(redeems) == 1
        assert sum(tx.amount for tx in session.exec(select(RewardTransaction)).all()) == 5


def test_jackpot_alone_returns_original_german_motivation(client, monkeypatch):
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    _complete(client, "One", priority=1)
    _complete(client, "Two", priority=1)
    monkeypatch.setattr(main, "arcade_rng", _FixedOutcomeRng("jackpot"))
    monkeypatch.setattr(main, "quote_rng", _FixedQuoteRng())
    response = client.post("/arcade/spin", json={"operation_id": "jackpot-op"})
    assert response.status_code == 200
    assert response.json()["chips_awarded"] == 100
    assert response.json()["quote"] == {
        "text": "Nicht nachdenken. Einen Schritt machen.",
        "attribution": None,
    }


def test_timezone_change_cannot_reset_an_active_day(client, monkeypatch):
    # At this instant Berlin is July 13 while Los Angeles is July 12.
    _clock(monkeypatch, datetime(2026, 7, 13, 0, 30))
    _complete(client, "Counted today")
    blocked = client.patch("/settings", json={"timezone": "America/Los_Angeles"})
    assert blocked.status_code == 409
    assert client.get("/settings").json()["timezone"] == "Europe/Berlin"
    # A zone that still maps to the same local date is safe.
    assert client.patch("/settings", json={"timezone": "UTC"}).status_code == 200


def test_berlin_local_day_bounds_are_dst_safe():
    zone = main.ZoneInfo("Europe/Berlin")
    spring_start, spring_end = main._day_bounds_utc(date(2026, 3, 29), zone)
    autumn_start, autumn_end = main._day_bounds_utc(date(2026, 10, 25), zone)
    assert spring_end - spring_start == timedelta(hours=23)
    assert autumn_end - autumn_start == timedelta(hours=25)


def _file_engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    init_db(engine, create_backup=False)
    with Session(engine) as session:
        settings = session.get(AppSettings, 1)
        settings.timezone = "Europe/Berlin"
        session.add(settings)
        session.commit()
    return engine


def test_concurrent_completion_awards_once(tmp_path, monkeypatch):
    engine = _file_engine(tmp_path / "concurrent-complete.db")
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    with Session(engine) as session:
        todo = Todo(title="Only once", priority=1)
        session.add(todo)
        session.commit()
        session.refresh(todo)
        todo_id = todo.id

    def complete_once():
        with Session(engine) as session:
            return main.update_todo(
                todo_id, main.TodoUpdate(status="done"), session
            ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: complete_once(), range(2))) == ["done", "done"]

    with Session(engine) as session:
        events = session.exec(
            select(ProductivityEvent).where(
                ProductivityEvent.event_type == "completion"
            )
        ).all()
        assert len(events) == 1
        assert events[0].lifetime_xp_delta == 50
        assert session.get(ArcadeState, 1).tickets == 0


def test_concurrent_redemptions_cannot_overspend(tmp_path):
    engine = _file_engine(tmp_path / "concurrent-redeem.db")
    with Session(engine) as session:
        session.add(
            RewardTransaction(
                event_key="seed-chips",
                transaction_type="test_award",
                amount=10,
                balance_after=10,
            )
        )
        goal = RewardGoal(title="Only one", cost_chips=10)
        session.add(goal)
        session.commit()
        session.refresh(goal)
        goal_id = goal.id

    def redeem(operation_id: str):
        with Session(engine) as session:
            try:
                result = main.redeem_reward_goal(
                    goal_id, main.OperationRequest(operation_id=operation_id), session
                )
                return 200, result.idempotent
            except HTTPException as error:
                return error.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(redeem, ("redeem-a", "redeem-b")))
    assert sorted(status for status, _ in results) == [200, 409]

    with Session(engine) as session:
        transactions = session.exec(select(RewardTransaction)).all()
        assert sum(transaction.amount for transaction in transactions) == 0
        assert sum(tx.transaction_type == "redeem" for tx in transactions) == 1


def test_concurrent_slot_requests_enforce_daily_cap_and_retain_fourth_ticket(
    tmp_path, monkeypatch
):
    engine = _file_engine(tmp_path / "concurrent-slots.db")
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    monkeypatch.setattr(main, "arcade_rng", _FixedOutcomeRng("quick_win"))
    with Session(engine) as session:
        state = session.get(ArcadeState, 1)
        state.tickets = 10
        session.add(state)
        session.commit()

    def spin_once(operation_id: str):
        with Session(engine) as session:
            try:
                result = main.spin_arcade(
                    main.OperationRequest(operation_id=operation_id), session
                )
                return 200, result.idempotent
            except HTTPException as error:
                return error.status_code, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(spin_once, ("slot-a", "slot-b", "slot-c", "slot-d")))
    assert sorted(status for status, _ in results) == [200, 200, 200, 429]

    with Session(engine) as session:
        assert session.get(ArcadeState, 1).tickets == 7
        assert len(session.exec(select(ArcadeSpin)).all()) == 3
        awards = session.exec(
            select(RewardTransaction).where(
                RewardTransaction.transaction_type == "slot_award"
            )
        ).all()
        assert len(awards) == 3
        assert sum(award.amount for award in awards) == 15


def test_concurrent_same_slot_operation_spends_and_awards_once(tmp_path, monkeypatch):
    engine = _file_engine(tmp_path / "idempotent-slot.db")
    _clock(monkeypatch, datetime(2026, 7, 13, 10, 0))
    monkeypatch.setattr(main, "arcade_rng", _FixedOutcomeRng("focus_15"))
    with Session(engine) as session:
        state = session.get(ArcadeState, 1)
        state.tickets = 1
        session.add(state)
        session.commit()

    def spin_same():
        with Session(engine) as session:
            result = main.spin_arcade(
                main.OperationRequest(operation_id="same-slot"), session
            )
            return result.idempotent, result.operation_id, result.chips_balance

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: spin_same(), range(2)))
    assert sorted(item[0] for item in results) == [False, True]
    assert {item[1] for item in results} == {"same-slot"}
    assert {item[2] for item in results} == {10}

    with Session(engine) as session:
        assert session.get(ArcadeState, 1).tickets == 0
        assert len(session.exec(select(ArcadeSpin)).all()) == 1
        awards = session.exec(
            select(RewardTransaction).where(
                RewardTransaction.transaction_type == "slot_award"
            )
        ).all()
        assert len(awards) == 1
        assert awards[0].amount == 10
