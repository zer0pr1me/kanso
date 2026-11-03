import asyncio
from datetime import date
from uuid import uuid4

import pytest
from textual.binding import Binding

from kanso.core.models import Habit, HabitDay, Repeat, Todo
from kanso.core.repeats import clean_rule
from kanso.tui.format import (
    HABIT_MARKS,
    LABEL_STYLE,
    RULE_STYLE,
    describe_rule,
    habit_day_label,
    human_date,
    section_title,
    template_label,
    todo_label,
)
from kanso.tui.panes import successor
from kanso.tui.widgets import UA_LAYOUT, LabelSuggester, with_layouts

WEDNESDAY = date(2026, 10, 21)


@pytest.mark.parametrize(
    "day, expected",
    [
        (date(2026, 10, 21), "Today"),
        (date(2026, 10, 22), "Tomorrow"),
        (date(2026, 10, 20), "Yesterday"),
        (date(2026, 10, 19), "Monday"),
        (date(2026, 10, 25), "Sunday"),
        (date(2026, 10, 26), "Next Monday"),
        (date(2026, 11, 1), "Next Sunday"),
        (date(2026, 10, 18), "Last Sunday"),
        (date(2026, 10, 12), "Last Monday"),
        (date(2026, 11, 2), "Nov 2"),
        (date(2026, 10, 11), "Oct 11"),
        (date(2027, 1, 5), "Jan 5, 2027"),
        (date(2025, 12, 31), "Dec 31, 2025"),
    ],
)
def test_human_date(day, expected):
    assert human_date(day, WEDNESDAY) == expected


@pytest.mark.parametrize(
    "today, day, expected",
    [
        (date(2026, 10, 19), date(2026, 10, 18), "Yesterday"),  # Monday: yesterday was last week
        (date(2026, 10, 25), date(2026, 10, 26), "Tomorrow"),  # Sunday: tomorrow is next week
        (date(2026, 10, 25), date(2026, 10, 19), "Monday"),
        (date(2026, 12, 31), date(2027, 1, 4), "Next Monday"),  # across a year boundary
        (date(2026, 12, 31), date(2027, 1, 11), "Jan 11, 2027"),
    ],
)
def test_human_date_at_week_edges(today, day, expected):
    assert human_date(day, today) == expected


def test_section_title_defaults_to_today():
    assert section_title(date.today()) == "Today"
    assert section_title(None) == "No date"


def spans(content) -> list[tuple[str, str]]:
    return [(content.plain[s.start : s.end], s.style) for s in content.spans]


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({}, "[ ] [b]milk"),
        ({"done": True}, "[x] [b]milk"),
        ({"repeat_id": uuid4()}, "[ ] [b]milk ↻"),
        ({"labels": ["errands", "[i]x"]}, "[ ] [b]milk  #errands   #[i]x "),
    ],
)
def test_todo_label_is_plain_text(fields, expected):
    content = todo_label(Todo(text="[b]milk", position="V", **fields))
    assert str(content) == expected
    assert spans(content) == [(f" #{label} ", LABEL_STYLE) for label in fields.get("labels", [])]


@pytest.mark.parametrize(
    "rule, expected",
    [
        ({"kind": "days"}, "Daily"),
        ({"kind": "days", "interval": 3}, "Every 3 days"),
        ({"kind": "weekdays", "weekdays": [4, 0, 2]}, "Mon, Wed, Fri"),
        ({"kind": "monthdays", "monthdays": [15, 1]}, "Monthly on 1, 15"),
    ],
)
def test_template_label_is_text_then_rule_chip(rule, expected):
    repeat = Repeat(text="[b]Water", position="V", **clean_rule(**rule))
    assert describe_rule(repeat) == expected
    assert str(template_label(repeat)) == f"[b]Water  {expected} "
    assert spans(template_label(repeat)) == [(f" {expected} ", RULE_STYLE)]


@pytest.mark.parametrize(
    "value, expected",
    [
        ("w", "work "),
        ("W", "work "),
        ("h", "Home "),
        ("home x", None),
        ("work w", "work workshop "),
        ("work ", None),
        ("", None),
        ("a  wo", "a  work "),
    ],
)
def test_label_suggester(value, expected):
    suggester = LabelSuggester(["errands", "Home", "work", "workshop"])
    assert asyncio.run(suggester.get_suggestion(value)) == expected


@pytest.mark.parametrize("removed, expected", [(0, "b"), (1, "c"), (2, "b")])
def test_successor_is_next_else_previous(removed, expected):
    records = [Todo(text=t, position="V") for t in "abc"]
    assert successor(records, records[removed]).text == expected


def test_no_successor_of_the_only_record():
    record = Todo(text="a", position="V")
    assert successor([record], record) is None


@pytest.mark.parametrize("state, mark", [(None, "[ ]"), ("success", "[✓]"), ("failure", "[✗]")])
def test_habit_day_label_is_its_mark_then_plain_title(state, mark):
    content = habit_day_label(HabitDay(text="[b]Read", position="V", day=date.today(), state=state))
    assert str(content) == f"{mark} [b]Read"
    assert spans(content) == ([(mark, HABIT_MARKS[state][1])] if state else [])


def test_habits_are_labelled_like_repeats():
    habit = Habit(text="Read", position="V", **clean_rule("weekdays", weekdays=[0]))
    assert str(template_label(habit)) == "Read  Mon "


# Keyboard layouts


def test_ukrainian_layout_types_on_the_same_keys():
    assert [UA_LAYOUT[k] for k in ["a", "e", "J", "G", "full_stop", "comma", "number_sign", "solidus"]] == [
        "ф",
        "у",
        "О",
        "П",
        "ю",
        "б",
        "numero_sign",
        "full_stop",
    ]


def test_with_layouts_adds_hidden_ukrainian_copies():
    original = Binding("J,shift+down", "move(1)", "Move", key_display="J/K")
    assert [(b.key, b.action, b.show) for b in with_layouts([("a", "add", "Add")])] == [("a", "add", True), ("ф", "add", False)]
    assert with_layouts([original])[1] == Binding("О", "move(1)", "Move", show=False)
    assert [(b.key, b.show) for b in with_layouts([("space", "toggle", "Done")])] == [("space", True)]
    assert [b.key for b in with_layouts([Binding("enter, y", "yes")])] == ["enter, y", "н"]
