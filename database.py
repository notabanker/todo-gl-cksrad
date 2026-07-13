"""SQLite engine + session wiring for TYCHE."""

from collections.abc import Iterator
import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

import models  # noqa: F401 - imported so SQLModel registers the Todo table

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


def init_db() -> None:
    """Create tables if they do not yet exist."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a DB session."""
    with Session(engine) as session:
        yield session
