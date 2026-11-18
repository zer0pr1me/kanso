from datetime import date, timedelta
from uuid import uuid4

import pytest

from kanso.core.models import HabitDay
from kanso.core.stats import Mark, streak, success_rate

TODAY = date(2026, 11, 17)


def days(*states: str | None, until: date = TODAY) -> list[HabitDay]:
    """Days of one habit, the last `until`, the others each a day before."""
    habit_id = uuid4()
    n = len(states)
    return [
        HabitDay(list_id=uuid4(), habit_id=habit_id, text="h", day=until - timedelta(days=n - 1 - i), state=s, position="a")
        for i, s in enumerate(states)
    ]


@pytest.mark.parametrize(
    "states, expected",
    [
        ((), 0),
        ((None,), 0),  # today alone, never over
        (("success", "success", None), 2),  # today doesn't count yet
        (("failure", "success", "success", None), 2),
        (("success", "failure", None), 0),
        (("success", "success", None, None, None), 2),  # unchecked newest days are skipped
        (("success", None, "success", None), 1),  # an unchecked day before the newest marked breaks it
        ((None, None, None), 0),
    ],
)
def test_streak(states, expected):
    assert streak(days(*states), TODAY) == expected


def test_streak_ignores_order_and_days_after_today():
    marked = days("failure", "success", "success", "failure", until=TODAY + timedelta(days=1))  # today, tomorrow
    assert streak(list(reversed(marked)), TODAY) == 2


def test_streak_counts_today_once_marked_as_a_repeating_todo_done_today():
    todo = [Mark(TODAY - timedelta(days=1), "success"), Mark(TODAY, "success")]
    assert streak(todo, TODAY) == 2 and streak([Mark(TODAY, None), *todo[:1]], TODAY) == 1
    assert success_rate([Mark(TODAY, "failure"), *todo]) == 2 / 3


@pytest.mark.parametrize(
    "states, expected",
    [((), None), ((None, None), None), (("success", "failure", None, "success", "success"), 0.75), (("failure",), 0)],
)
def test_success_rate_counts_only_marked_days(states, expected):
    assert success_rate(days(*states)) == expected
