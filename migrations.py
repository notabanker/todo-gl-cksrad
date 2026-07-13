"""Versioned, idempotent SQLite migrations for local user data.

The application started with an unversioned ``todos``/``arcade_state``
database.  This runner upgrades it in one immediate transaction and creates a
verified sibling backup first. New databases are created directly at the
latest version and do not need a backup.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

import models  # noqa: F401 - register every SQLModel table for direct runner use


LATEST_SCHEMA_VERSION = 1


def system_timezone_name() -> str:
    """Best-effort IANA timezone, with a safe Europe/Berlin fallback."""
    candidates: list[str] = []
    if os.environ.get("TZ"):
        candidates.append(os.environ["TZ"])
    local_tz = datetime.now().astimezone().tzinfo
    key = getattr(local_tz, "key", None)
    if key:
        candidates.append(key)
    try:
        resolved = Path("/etc/localtime").resolve()
        marker = "/zoneinfo/"
        if marker in str(resolved):
            candidates.append(str(resolved).split(marker, 1)[1])
    except OSError:
        pass
    candidates.append("Europe/Berlin")
    for candidate in candidates:
        try:
            ZoneInfo(candidate)
            return candidate
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return "UTC"


def _database_path(engine: Engine) -> Path | None:
    value = engine.url.database
    if not value or value == ":memory:":
        return None
    query = engine.url.query
    if str(query.get("mode", "")).lower() == "memory":
        return None
    if str(value).lower().startswith("file:") and "mode=memory" in str(value).lower():
        return None
    return Path(value).expanduser().resolve()


def _ensure_private_primary_database(path: Path) -> None:
    """Create or harden the primary DB before SQLite can read or write it."""
    flags = os.O_WRONLY | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags, 0o600)
    try:
        # Creation starts with permissions no broader than 0600 even under a
        # permissive umask; fchmod also repairs existing legacy databases.
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _reserve_private_backup(path: Path, from_version: int) -> Path:
    """Atomically reserve a mode-0600 path before SQLite can write to it."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = path.suffix if path.suffix in {".db", ".sqlite", ".sqlite3"} else ".sqlite3"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    # A retry suffix makes repeated migration attempts in the same second safe,
    # while O_EXCL prevents following or replacing a pre-existing path.
    for attempt in range(1000):
        collision_suffix = "" if attempt == 0 else f".{attempt}"
        backup = path.with_name(
            f"{path.stem}.pre-v{from_version}.{stamp}{collision_suffix}{suffix}"
        )
        try:
            descriptor = os.open(backup, flags, 0o600)
        except FileExistsError:
            continue
        try:
            # 0600 is already the most permissive possible creation mode. The
            # explicit fchmod also normalizes a more restrictive process umask.
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return backup
    raise RuntimeError("could not reserve a unique database backup path")


def _remove_incomplete_backup(path: Path) -> None:
    """Best-effort cleanup of an unverified backup and SQLite sidecars."""
    for candidate in (
        Path(f"{path}-journal"),
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        path,
    ):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # Preserve the exception that made the backup invalid. Any file
            # left behind was still private from the instant it was created.
            pass


def _backup_legacy_database(path: Path, from_version: int) -> Path:
    """Create and verify a WAL-consistent, mode-0600 migration backup."""
    backup = _reserve_private_backup(path, from_version)
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as source:
            with sqlite3.connect(backup) as destination:
                source.backup(destination)
                check = destination.execute("PRAGMA quick_check").fetchone()
                if not check or check[0] != "ok":
                    raise RuntimeError("database backup failed integrity verification")
        # Defense in depth: SQLite writes the pre-created inode in place, but
        # normalize its final mode as well before returning it to the caller.
        backup.chmod(0o600)
        return backup
    except BaseException:
        _remove_incomplete_backup(backup)
        raise


def _columns(connection, table: str) -> set[str]:
    rows = connection.exec_driver_sql(f'PRAGMA table_info("{table}")').all()
    return {str(row[1]) for row in rows}


def _parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    raise ValueError(f"unsupported datetime value: {value!r}")


def _legacy_points(priority: int, created, completed) -> int:
    created_at = _parse_datetime(created)
    completed_at = _parse_datetime(completed)
    base = {1: 50, 2: 30, 3: 20}.get(int(priority), 20)
    waited_days = max(0, int((completed_at - created_at).total_seconds() // 86400))
    return base + min(30, waited_days * 2)


def _legacy_completion_key(todo_id: int, created) -> str:
    created_at = _parse_datetime(created).isoformat(timespec="microseconds")
    return f"todo:{todo_id}:created:{created_at}:completion"


def _add_todo_columns(connection) -> None:
    existing = _columns(connection, "todos")
    additions = {
        "task_kind": "VARCHAR NOT NULL DEFAULT 'one_off'",
        "routine_id": "INTEGER REFERENCES routines(id) ON DELETE SET NULL",
        "occurrence_date": "DATE",
        "available_at": "DATETIME",
        "unavailable_seconds": "INTEGER NOT NULL DEFAULT 0",
        "first_completed_at": "DATETIME",
    }
    for name, declaration in additions.items():
        if name not in existing:
            connection.exec_driver_sql(
                f'ALTER TABLE todos ADD COLUMN "{name}" {declaration}'
            )


def _add_arcade_spin_columns(connection) -> None:
    existing = _columns(connection, "arcade_spins")
    if "spin_count" not in existing:
        connection.exec_driver_sql(
            "ALTER TABLE arcade_spins ADD COLUMN spin_count INTEGER NOT NULL DEFAULT 0"
        )


def _add_productivity_event_columns(connection) -> None:
    existing = _columns(connection, "productivity_events")
    if "operation_id" not in existing:
        connection.exec_driver_sql(
            "ALTER TABLE productivity_events ADD COLUMN operation_id VARCHAR"
        )


def _add_reward_transaction_columns(connection) -> None:
    existing = _columns(connection, "reward_transactions")
    if "goal_title" not in existing:
        connection.exec_driver_sql(
            "ALTER TABLE reward_transactions ADD COLUMN goal_title VARCHAR"
        )


def _create_indexes(connection) -> None:
    statements = (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_todos_routine_occurrence "
        "ON todos(routine_id, occurrence_date) "
        "WHERE routine_id IS NOT NULL AND occurrence_date IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_todos_status_available "
        "ON todos(status, available_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_productivity_event_key "
        "ON productivity_events(event_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_productivity_operation_id "
        "ON productivity_events(operation_id) WHERE operation_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_reward_transaction_event_key "
        "ON reward_transactions(event_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_arcade_spin_operation_id "
        "ON arcade_spins(operation_id)",
    )
    for statement in statements:
        connection.exec_driver_sql(statement)


def _backfill_legacy_events(connection, timezone_name: str) -> None:
    connection.exec_driver_sql(
        "UPDATE todos SET first_completed_at = COALESCE(completed_at, updated_at) "
        "WHERE status = 'done' AND first_completed_at IS NULL"
    )
    zone = ZoneInfo(timezone_name)
    rows = connection.exec_driver_sql(
        "SELECT id, priority, created_at, updated_at, completed_at, first_completed_at "
        "FROM todos WHERE status = 'done' ORDER BY id"
    ).mappings()
    for row in rows:
        completed = row["first_completed_at"] or row["completed_at"] or row["updated_at"]
        completed_at = _parse_datetime(completed)
        aware_utc = completed_at.replace(tzinfo=timezone.utc)
        local_date = aware_utc.astimezone(zone).date().isoformat()
        points = _legacy_points(row["priority"], row["created_at"], completed)
        event_key = _legacy_completion_key(row["id"], row["created_at"])
        connection.exec_driver_sql(
            "INSERT INTO productivity_events "
            "(event_key, event_type, todo_id, routine_id, local_date, "
            "lifetime_xp_delta, daily_score_delta, streak_count, details_json, created_at) "
            "SELECT ?, 'legacy_completion', ?, NULL, ?, ?, 0, 0, NULL, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM productivity_events WHERE event_key = ?)",
            (
                event_key,
                row["id"],
                local_date,
                points,
                completed_at,
                event_key,
            ),
        )

    existing_state = connection.exec_driver_sql(
        "SELECT id, highest_xp_threshold FROM arcade_state WHERE id = 1"
    ).first()
    if existing_state is None:
        connection.exec_driver_sql(
            "INSERT INTO arcade_state "
            "(id, highest_xp_threshold, tickets, spin_count, updated_at) "
            "VALUES (1, 0, 0, 0, ?)",
            (datetime.now(timezone.utc).replace(tzinfo=None),),
        )
        existing_threshold = 0
    else:
        existing_threshold = int(existing_state[1])

    lifetime_xp = int(
        connection.exec_driver_sql(
            "SELECT COALESCE(SUM(lifetime_xp_delta), 0) FROM productivity_events"
        ).scalar_one()
    )
    if existing_threshold > lifetime_xp:
        floor_delta = existing_threshold - lifetime_xp
        connection.exec_driver_sql(
            "INSERT INTO productivity_events "
            "(event_key, event_type, todo_id, routine_id, local_date, "
            "lifetime_xp_delta, daily_score_delta, streak_count, details_json, created_at) "
            "SELECT 'migration:v1:legacy-xp-floor', 'legacy_xp_floor', NULL, NULL, ?, ?, 0, 0, NULL, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM productivity_events "
            "WHERE event_key = 'migration:v1:legacy-xp-floor')",
            (
                datetime.now(zone).date().isoformat(),
                floor_delta,
                datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        )
        lifetime_xp = existing_threshold
    accounted_threshold = max(existing_threshold, (lifetime_xp // 100) * 100)
    # Historical XP is baselined, never minted again. Existing ticket and spin
    # balances remain untouched byte-for-byte.
    connection.exec_driver_sql(
        "UPDATE arcade_state SET highest_xp_threshold = ? WHERE id = 1",
        (accounted_threshold,),
    )


def migrate_database(engine: Engine, *, create_backup: bool = True) -> Path | None:
    """Upgrade one SQLite engine and return the backup path, if one was made."""
    database_path = _database_path(engine)
    backup_path: Path | None = None
    if database_path is not None:
        # This must happen before engine.connect(): a fresh SQLite connection
        # otherwise creates the primary file using the process's default mode.
        _ensure_private_primary_database(database_path)
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            version = int(
                connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            )
            if version > LATEST_SCHEMA_VERSION:
                raise RuntimeError("database was created by a newer application version")
            tables = {
                str(row[0])
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).all()
            }
            if tables and "todos" not in tables:
                raise RuntimeError("unrecognized non-empty database schema")

            if version < LATEST_SCHEMA_VERSION and tables and create_backup and database_path:
                backup_path = _backup_legacy_database(database_path, version)

            # New tables are safe to create before adding references from the
            # legacy Todo table; create_all deliberately leaves existing tables.
            SQLModel.metadata.create_all(connection)
            _add_todo_columns(connection)
            _add_arcade_spin_columns(connection)
            _add_productivity_event_columns(connection)
            _add_reward_transaction_columns(connection)
            _create_indexes(connection)

            timezone_name = system_timezone_name()
            connection.exec_driver_sql(
                "INSERT INTO app_settings (id, timezone, updated_at) "
                "SELECT 1, ?, ? WHERE NOT EXISTS "
                "(SELECT 1 FROM app_settings WHERE id = 1)",
                (
                    timezone_name,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
            if version < LATEST_SCHEMA_VERSION:
                _backfill_legacy_events(connection, timezone_name)
            connection.exec_driver_sql(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return backup_path
