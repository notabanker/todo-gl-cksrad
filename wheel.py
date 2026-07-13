"""Weighted-random pick logic for the wheel of fortune.

Pure functions only (no FastAPI, no DB) so the age-weighting can be unit-tested
and its distribution verified without spinning up the app. ``now`` is injected
by callers to keep these deterministic-friendly.
"""

import random
from datetime import datetime
from typing import Sequence

MIN_WEIGHT = 1


def weight_for(created_at: datetime, now: datetime) -> int:
    """Weight of a todo: its age in whole days, floored at ``MIN_WEIGHT``.

    Older todos get a higher weight and are therefore more likely to be picked.
    """
    return max(MIN_WEIGHT, (now - created_at).days)


def pick_weighted(todos: Sequence, now: datetime, rng: random.Random = random):
    """Pick one todo at random, weighted by age. Returns ``None`` if empty.

    ``rng`` is injectable so tests can seed it for reproducibility.
    """
    if not todos:
        return None
    weights = [weight_for(todo.created_at, now) for todo in todos]
    return rng.choices(list(todos), weights=weights, k=1)[0]
