"""How records read on screen, as plain text: user text is never parsed as markup."""

import calendar
from datetime import date

from textual.content import Content

from kanso.core.models import HabitDay, Scheduled, Todo
from kanso.core.order import human_date

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
