"""How records read on screen, as plain text: user text is never parsed as markup."""

import calendar
from datetime import date, timedelta
from itertools import groupby

from textual.content import Content

from kanso.core.models import Habit, HabitDay, Scheduled, Todo, TodoList
from kanso.core.order import human_date
from kanso.core.stats import streak, success_rate

LABEL_STYLE = "bold $text-accent on $accent-muted"
RULE_STYLE = "$text-secondary on $secondary-muted"


def section_title(day: date | None) -> str:
    return human_date(day) if day else "No date"


def chip(text: str, style: str) -> tuple[str, tuple[str, str]]:
    return (" ", (f" {text} ", style))


def todo_label(todo: Todo) -> Content:
    """Checkbox and title, ↻ if repeating, then each label as a `#label` chip."""
    repeat = " ↻" if todo.repeat_id else ""
    chips = [part for label in todo.labels for part in chip(f"#{label}", LABEL_STYLE)]
    return Content.assemble(f"{'[x]' if todo.done else '[ ]'} {todo.title}{repeat}", *chips)


def describe_rule(template: Scheduled) -> str:
    match template.kind:
        case "days":
            return "Daily" if template.interval == 1 else f"Every {template.interval} days"
        case "weekdays":
            return ", ".join(calendar.day_abbr[d] for d in template.weekdays)
        case _:
            return "Monthly on " + ", ".join(map(str, template.monthdays))


def template_label(template: Scheduled) -> Content:
    """Text of a repeat or habit, then its rule as a chip."""
    return Content.assemble(template.text, *chip(describe_rule(template), RULE_STYLE))


HABIT_MARKS = {None: ("[ ]", ""), "success": ("[✓]", "bold $text-success"), "failure": ("[✗]", "bold $text-error")}


def habit_day_label(habit_day: HabitDay) -> Content:
    """Its state's mark, then its habit's title."""
    return Content.assemble(HABIT_MARKS[habit_day.state], f" {habit_day.title}")


NAME_WIDTH = 24
WEEK_WIDTH = 14
STATS = "  {:>6}  {:>4}"
ACCENT_STYLE = "bold $text-accent"


def fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


GRID_MARKS = {state: (f"{HABIT_MARKS[state][0][1]} ", HABIT_MARKS[state][1]) for state in ("success", "failure")}
GRID_MARKS[None] = ("· ", "dim")
"""A day's mark in the grid, by its state: a dot while unchecked; days not due are blank."""


def habit_grid(history: list[tuple[TodoList, Habit, list[HabitDay]]], today: date, width: int, back: int = 0) -> Content:
    """`Store.habit_history` as a grid of whole weeks, Monday to Sunday, `back` weeks before
    this one's, as many as fit in `width`: a column per day, a row per habit under its list's
    name, then its streak and its success rate over the days shown; a legend last."""
    if not history:
        return Content.styled("No habits yet", "dim italic")
    name_width = 3 + min(NAME_WIDTH, max(len(habit.text) for _, habit, _ in history))
    weeks = max(1, (width - name_width - len(STATS.format("", ""))) // WEEK_WIDTH)
    end = today + timedelta(days=6 - today.weekday(), weeks=-back)
    columns = [end - timedelta(days=n) for n in range(7 * weeks - 1, -1, -1)]
    pad = " " * name_width
    letters = [(f"{calendar.day_abbr[d.weekday()][0]} ", ACCENT_STYLE if d == today else "dim") for d in columns]
    lines = [
        Content(pad + "".join(f"{d:%b} {d.day}".ljust(WEEK_WIDTH) for d in columns[::7]).rstrip()),
        Content.assemble(pad, *letters, STATS.format("Streak", "Rate")),
    ]
    for todo_list, habits in groupby(history, lambda entry: entry[0]):
        lines.append(Content.styled(fit(todo_list.name, width), ACCENT_STYLE))
        for _, habit, days in habits:
            by_day = {d.day: d for d in days}
            shown = [by_day[d] for d in columns if d in by_day]
            rate = success_rate(shown)
            stats = STATS.format(streak(days, today), "—" if rate is None else f"{rate:.0%}")
            name = f"  {fit(habit.text, name_width - 3)}".ljust(name_width)
            lines.append(Content.assemble(name, *(GRID_MARKS[by_day[d].state] if d in by_day else "  " for d in columns), stats))
    legend = (Content.assemble(mark, (state or "unchecked", "dim")) for state, mark in GRID_MARKS.items())
    lines += [Content(""), Content("   ").join(legend)]
    return Content("\n").join(lines)
