"""How todos are shown: grouped by date, and autosorted within each date."""

from datetime import date
from itertools import groupby

from kanso.core.models import Todo


def autosort_key(todo: Todo) -> tuple:
    """Order within a date, one rule per element: done todos first. Ties keep list order."""
    return (not todo.done,)


def sections(todos: list[Todo]) -> list[tuple[date | None, list[Todo]]]:
    """Todos in list order grouped by scheduled date, earliest first and unscheduled last, each autosorted."""
    by_date = sorted(todos, key=lambda t: (t.scheduled_at is None, t.scheduled_at or date.min, *autosort_key(t)))
    return [(day, list(group)) for day, group in groupby(by_date, lambda t: t.scheduled_at)]
