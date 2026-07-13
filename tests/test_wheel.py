"""Unit + distribution tests for the age-weighting logic."""

import random
from datetime import datetime, timedelta

import wheel


class FakeTodo:
    """Minimal stand-in with just the attribute the wheel reads."""

    def __init__(self, title: str, created_at: datetime):
        self.title = title
        self.created_at = created_at


NOW = datetime(2026, 7, 13, 0, 0, 0)


def test_weight_is_age_in_days():
    assert wheel.weight_for(NOW - timedelta(days=30), NOW) == 30
    assert wheel.weight_for(NOW - timedelta(days=10), NOW) == 10


def test_weight_floored_at_one():
    # A todo younger than a day still gets weight 1 (never 0).
    assert wheel.weight_for(NOW - timedelta(hours=3), NOW) == 1
    assert wheel.weight_for(NOW, NOW) == 1


def test_pick_weighted_empty_returns_none():
    assert wheel.pick_weighted([], NOW) is None


def test_older_todos_are_picked_more_often():
    """The core Phase 0 guarantee: pick frequency tracks age ordering."""
    todos = [
        FakeTodo("old", NOW - timedelta(days=30)),
        FakeTodo("mid", NOW - timedelta(days=10)),
        FakeTodo("new", NOW - timedelta(days=1)),
    ]
    rng = random.Random(1234)  # seeded for reproducibility
    counts = {"old": 0, "mid": 0, "new": 0}
    spins = 5000
    for _ in range(spins):
        counts[wheel.pick_weighted(todos, NOW, rng=rng).title] += 1

    # Ordering by pick frequency must match ordering by age.
    assert counts["old"] > counts["mid"] > counts["new"]
    # Weights are 30/10/1 (~73%/24%/2%); assert the old one dominates clearly.
    assert counts["old"] / spins > 0.6
