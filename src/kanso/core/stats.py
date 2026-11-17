"""Habit statistics, from a habit's days, for every interface."""

from datetime import date

from kanso.core.models import HabitDay


def streak(days: list[HabitDay], today: date) -> int:
    """Successes in a row up to the newest day that's over, skipping the newest days while
    they're still unchecked (not marked yet), so a streak lasts until a day breaks it."""
    over = sorted((d for d in days if d.day < today), key=lambda d: d.day, reverse=True)
    marked = [d.state for d in over][next((i for i, d in enumerate(over) if d.state), len(over)) :]
    return next((i for i, state in enumerate(marked) if state != "success"), len(marked))


def success_rate(days: list[HabitDay]) -> float | None:
    """The share of the marked days that are successes; `None` if none is marked."""
    marked = [d.state for d in days if d.state]
    return marked.count("success") / len(marked) if marked else None
