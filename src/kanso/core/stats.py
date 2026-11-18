"""Statistics of habits and repeating todos, from their marks, for every interface."""

from collections.abc import Sequence
from datetime import date
from typing import NamedTuple

from kanso.core.models import HabitDay


class Mark(NamedTuple):
    """A repeating todo as statistics see it: the day it was for, and `"success"` if done,
    `"failure"` if it wasn't by its date, `None` while it's still to do."""

    day: date
    state: str | None


type Marked = HabitDay | Mark


def streak(days: Sequence[Marked], today: date) -> int:
    """Successes in a row up to today, skipping the newest days while they're still unchecked
    (not marked yet, as a habit's today always is), so a streak lasts until a day breaks it."""
    over = sorted((d for d in days if d.day <= today), key=lambda d: d.day, reverse=True)
    marked = [d.state for d in over][next((i for i, d in enumerate(over) if d.state), len(over)) :]
    return next((i for i, state in enumerate(marked) if state != "success"), len(marked))


def success_rate(days: Sequence[Marked]) -> float | None:
    """The share of the marked days that are successes; `None` if none is marked."""
    marked = [d.state for d in days if d.state]
    return marked.count("success") / len(marked) if marked else None
