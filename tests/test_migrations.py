"""File-level safety tests for the legacy SQLite upgrade path."""

from datetime import datetime
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest
from sqlalchemy import event
from sqlmodel import Session, create_engine, select

import main
import migrations
from migrations import LATEST_SCHEMA_VERSION, migrate_database
from models import (
    AppSettings,
    ArcadeState,
    ProductivityEvent,
    RewardGoal,
    RewardTransaction,
    Todo,
)


def _create_legacy_database(
    path: Path,
    *,
    highest_xp_threshold: int = 100,
    tickets: int = 3,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE todos (
                id INTEGER NOT NULL PRIMARY KEY,
                title VARCHAR NOT NULL,
                description VARCHAR,
                tags VARCHAR,
                priority INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                completed_at DATETIME
            );
            CREATE TABLE arcade_state (
                id INTEGER NOT NULL PRIMARY KEY,
                highest_xp_threshold INTEGER NOT NULL,
                tickets INTEGER NOT NULL,
                spin_count INTEGER NOT NULL,
                last_outcome VARCHAR,
                last_symbols VARCHAR,
                last_label VARCHAR,
                last_message VARCHAR,
                last_tier VARCHAR,
                last_spun_at DATETIME,
                updated_at DATETIME NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO todos "
            "(id, title, priority, status, created_at, updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    1,
                    "Legacy P1",
                    1,
                    "done",
                    "2026-07-10 10:00:00",
                    "2026-07-10 10:00:00",
                    "2026-07-10 10:00:00",
                ),
                (
                    2,
                    "Legacy old P2",
                    2,
                    "done",
                    "2026-06-20 10:00:00",
                    "2026-07-10 10:00:00",
                    "2026-07-10 10:00:00",
                ),
                (
                    3,
                    "Still open",
                    3,
                    "open",
                    "2026-07-09 10:00:00",
                    "2026-07-09 10:00:00",
                    None,
                ),
            ),
        )
        connection.execute(
            "INSERT INTO arcade_state "
            "(id, highest_xp_threshold, tickets, spin_count, last_outcome, "
            "last_symbols, last_label, last_message, last_tier, last_spun_at, updated_at) "
            "VALUES (1, ?, ?, 7, 'jackpot', '[\"7\",\"7\",\"7\"]', "
            "'Old jackpot', 'Preserve me', 'jackpot', "
            "'2026-07-10 11:00:00', '2026-07-10 11:00:00')",
            (highest_xp_threshold, tickets),
        )
        connection.commit()


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def test_direct_runner_backs_up_migrates_and_restarts_without_data_loss(tmp_path):
    database = tmp_path / "legacy.db"
    _create_legacy_database(database)

    # Run in a clean interpreter to prove migrations.py registers models by
    # itself rather than depending on database.py import order.
    script = (
        "from sqlmodel import create_engine; "
        "from migrations import migrate_database; "
        "import sys; "
        "p=migrate_database(create_engine('sqlite:///'+sys.argv[1])); "
        "print(p or '')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(database)],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    backup = Path(result.stdout.strip())
    assert backup.exists()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert _table_names(backup) == {"todos", "arcade_state"}

    engine = create_engine(f"sqlite:///{database}")
    assert migrate_database(engine) is None  # versioned restart is idempotent
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        todo_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(todos)")
        }
        assert {
            "task_kind",
            "routine_id",
            "occurrence_date",
            "available_at",
            "unavailable_seconds",
            "first_completed_at",
        } <= todo_columns
        productivity_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(productivity_events)")
        }
        assert "operation_id" in productivity_columns
        reward_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(reward_transactions)")
        }
        assert "goal_title" in reward_columns

    with Session(engine) as session:
        assert [todo.title for todo in session.exec(select(Todo).order_by(Todo.id))] == [
            "Legacy P1",
            "Legacy old P2",
            "Still open",
        ]
        events = session.exec(select(ProductivityEvent)).all()
        completions = [event for event in events if event.event_type == "legacy_completion"]
        assert len(completions) == 2
        assert sum(event.lifetime_xp_delta for event in completions) == 110
        assert len({event.event_key for event in completions}) == 2
        state = session.get(ArcadeState, 1)
        assert state.highest_xp_threshold == 100
        assert state.tickets == 3
        assert state.spin_count == 7
        assert state.last_outcome == "jackpot"
        assert state.last_message == "Preserve me"
        assert session.get(AppSettings, 1) is not None

        # New ledgers/catalog survive another process-style restart as well.
        goal = RewardGoal(title="Migrated reward", cost_chips=25)
        session.add(goal)
        session.flush()
        session.add(
            RewardTransaction(
                event_key="restart-award",
                transaction_type="test_award",
                amount=25,
                balance_after=25,
            )
        )
        session.commit()

    engine.dispose()
    restarted = create_engine(f"sqlite:///{database}")
    assert migrate_database(restarted) is None
    with Session(restarted) as session:
        assert session.exec(select(RewardGoal)).one().title == "Migrated reward"
        assert session.exec(select(RewardTransaction)).one().amount == 25
        assert len(
            session.exec(
                select(ProductivityEvent).where(
                    ProductivityEvent.event_type == "legacy_completion"
                )
            ).all()
        ) == 2


def test_backup_is_mode_0600_before_sqlite_copies_any_task_data(
    tmp_path, monkeypatch
):
    database = tmp_path / "private-before-copy.db"
    _create_legacy_database(database)
    observed_modes = []
    real_connect = sqlite3.connect

    class ObservedSourceConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def backup(self, destination, *args, **kwargs):
            destination_path = Path(
                destination.execute("PRAGMA database_list").fetchone()[2]
            )
            observed_modes.append(stat.S_IMODE(destination_path.stat().st_mode))
            return self.connection.backup(destination, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def observing_connect(database_name, *args, **kwargs):
        connection = real_connect(database_name, *args, **kwargs)
        if kwargs.get("uri") and str(database_name).endswith("?mode=ro"):
            return ObservedSourceConnection(connection)
        return connection

    monkeypatch.setattr(migrations.sqlite3, "connect", observing_connect)
    backup = migrations._backup_legacy_database(database, from_version=0)

    assert observed_modes == [0o600]
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    with real_connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 3


@pytest.mark.parametrize("legacy", [False, True])
def test_primary_database_is_private_before_connect_during_writes_and_after_startup(
    tmp_path, legacy
):
    database = tmp_path / ("legacy-primary.db" if legacy else "fresh-primary.db")
    if legacy:
        _create_legacy_database(database)
        database.chmod(0o644)
    else:
        assert not database.exists()

    engine = create_engine(f"sqlite:///{database}")
    connect_modes = []
    write_modes = []

    @event.listens_for(engine, "connect")
    def observe_connect_mode(_dbapi_connection, _connection_record):
        connect_modes.append(stat.S_IMODE(database.stat().st_mode))

    @event.listens_for(engine, "before_cursor_execute")
    def observe_write_mode(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        operation = statement.lstrip().split(None, 1)[0].upper()
        if operation in {"ALTER", "CREATE", "INSERT", "UPDATE"}:
            write_modes.append(stat.S_IMODE(database.stat().st_mode))

    migrate_database(engine)

    assert connect_modes and connect_modes[0] == 0o600
    assert write_modes and set(write_modes) == {0o600}
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0] == 1
        if legacy:
            assert connection.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 3


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///:memory:",
        "sqlite:///file:private-primary-memory?mode=memory&cache=shared&uri=true",
    ],
)
def test_in_memory_databases_do_not_create_or_chmod_primary_files(
    database_url, monkeypatch
):
    def fail_if_called(_path):
        raise AssertionError("in-memory databases must not be treated as files")

    monkeypatch.setattr(migrations, "_ensure_private_primary_database", fail_if_called)
    engine = create_engine(database_url)
    migrate_database(engine)
    with Session(engine) as session:
        assert session.get(AppSettings, 1) is not None


def test_migration_rolls_back_schema_and_rows_if_an_upgrade_step_fails(
    tmp_path, monkeypatch
):
    database = tmp_path / "rollback.db"
    _create_legacy_database(database)
    engine = create_engine(f"sqlite:///{database}")

    def fail_indexes(_connection):
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(migrations, "_create_indexes", fail_indexes)
    with pytest.raises(RuntimeError, match="simulated migration failure"):
        migrate_database(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(todos)")
        } == {
            "id",
            "title",
            "description",
            "tags",
            "priority",
            "status",
            "created_at",
            "updated_at",
            "completed_at",
        }
        assert connection.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 3
        assert _table_names(database) == {"todos", "arcade_state"}


def test_high_water_floor_preserves_old_tickets_without_reminting(tmp_path):
    database = tmp_path / "highwater.db"
    _create_legacy_database(database, highest_xp_threshold=300, tickets=2)
    engine = create_engine(f"sqlite:///{database}")
    migrate_database(engine)

    with Session(engine) as session:
        state = session.get(ArcadeState, 1)
        assert state.highest_xp_threshold == 300
        assert state.tickets == 2
        assert main._lifetime_xp(session) == 300
        floor = session.exec(
            select(ProductivityEvent).where(
                ProductivityEvent.event_type == "legacy_xp_floor"
            )
        ).one()
        assert floor.lifetime_xp_delta == 190

        for index in range(2):
            completed = datetime(2026, 7, 11, 10, 0, index)
            session.add(
                Todo(
                    title=f"New completion {index}",
                    priority=1,
                    status="done",
                    created_at=completed,
                    updated_at=completed,
                    completed_at=completed,
                    first_completed_at=completed,
                )
            )
        session.flush()
        state, _, lifetime = main._reconcile_arcade(session)
        session.commit()
        assert lifetime == 400
        assert state.highest_xp_threshold == 400
        assert state.tickets == 3  # only the genuinely new 400-XP boundary


def test_migrated_legacy_completions_feed_today_and_streak_metrics(
    tmp_path, monkeypatch
):
    database = tmp_path / "legacy-metrics.db"
    _create_legacy_database(database)
    engine = create_engine(f"sqlite:///{database}")
    migrate_database(engine)
    with Session(engine) as session:
        settings = session.get(AppSettings, 1)
        settings.timezone = "Europe/Berlin"
        session.add(settings)
        session.commit()

    monkeypatch.setattr(main, "utcnow", lambda: datetime(2026, 7, 10, 18, 0))
    with Session(engine) as session:
        metrics = main.metrics(session)
    assert metrics["completed_today"] == 2
    assert metrics["completion_streak"] == 1
    assert metrics["lifetime_xp"] == 110
