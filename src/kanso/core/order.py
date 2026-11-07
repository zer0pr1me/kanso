"""How todos are shown: grouped by date, and autosorted within each date; how dates read."""

from datetime import date
from itertools import groupby

from kanso.core.models import Todo


def autosort_key(todo: Todo) -> tuple:
    """Order within a date, one rule per element: done todos first. Ties keep list order."""
    return (not todo.done,)


def done_before(todo: Todo, day: date) -> bool:
    """Done on an earlier day than `day`, so it can be hidden as old."""
    return todo.done and todo.completed_at is not None and todo.completed_at < day


def sections(todos: list[Todo]) -> list[tuple[date | None, list[Todo]]]:
    """Todos in list order grouped by scheduled date, earliest first and unscheduled last, each autosorted."""
    by_date = sorted(todos, key=lambda t: (t.scheduled_at is None, t.scheduled_at or date.min, *autosort_key(t)))
    return [(day, list(group)) for day, group in groupby(by_date, lambda t: t.scheduled_at)]


def human_date(day: date, today: date | None = None) -> str:
    """Today/Tomorrow/Yesterday; a weekday within this week (Monday to Sunday), prefixed
    Next/Last in the adjacent weeks; else month and day, plus the year if not this one."""
    today = today or date.today()
    match (day - today).days:
        case 0:
            return "Today"
        case 1:
            return "Tomorrow"
        case -1:
            return "Yesterday"
    weeks = (day.toordinal() - day.weekday() - today.toordinal() + today.weekday()) // 7
    prefix = {-1: "Last ", 0: "", 1: "Next "}.get(weeks)
    if prefix is not None:
        return f"{prefix}{day:%A}"
    return f"{day:%b} {day.day}" + ("" if day.year == today.year else f", {day.year}")
