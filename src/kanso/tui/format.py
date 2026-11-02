"""How records read on screen, as plain text: user text is never parsed as markup."""

import calendar
from datetime import date

from textual.content import Content

from kanso.core.models import HabitDay, Scheduled, Todo

LABEL_STYLE = "bold $text-accent on $accent-muted"
RULE_STYLE = "$text-secondary on $secondary-muted"


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
