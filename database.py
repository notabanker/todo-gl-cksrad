"""SQLite engine + session wiring for To-Do Gambling."""

from collections.abc import Iterator
import os
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, create_engine

import models  # noqa: F401 - register all table models before migration
from migrations import migrate_database

database_path_setting = os.environ.get("TYCHE_DB_PATH")
if database_path_setting:
    database_path = Path(database_path_setting).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{database_path}"
else:
    # Keep the existing repo-local database for ./run.sh and development.
    DATABASE_URL = "sqlite:///tyche.db"

# check_same_thread=False lets the same SQLite connection be used across the
# threads FastAPI/uvicorn may serve requests on.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.close()


def init_db(target_engine=None, *, create_backup: bool = True):
    """Create or transactionally migrate a database to the latest schema."""
    selected_engine = target_engine or engine
    return migrate_database(selected_engine, create_backup=create_backup)


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a DB session."""
    with Session(engine) as session:
        yield session
