"""Seed a few backdated open todos to demonstrate age-weighting.

Run with ``python seed.py``. Inserts three open todos aged ~30d / ~10d / ~1d so
that a series of spins visibly favours the older ones.
"""

from datetime import timedelta

from sqlmodel import Session

from database import engine, init_db
from models import STATUS_OPEN, Todo, utcnow

SEED_AGES_DAYS = {
    "Ancient chore (30d)": 30,
    "Middle-aged task (10d)": 10,
    "Fresh idea (1d)": 1,
}


def seed() -> None:
    init_db()
    now = utcnow()
    with Session(engine) as session:
        for title, age_days in SEED_AGES_DAYS.items():
            created = now - timedelta(days=age_days)
            session.add(
                Todo(
                    title=title,
                    status=STATUS_OPEN,
                    created_at=created,
                    updated_at=created,
                )
            )
        session.commit()
    print(f"Seeded {len(SEED_AGES_DAYS)} backdated open todos.")


if __name__ == "__main__":
    seed()
