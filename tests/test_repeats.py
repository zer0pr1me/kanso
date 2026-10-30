from datetime import date, timedelta

import pytest

from kanso.core.models import Repeat
from kanso.core.repeats import clean_rule, occurs_on

MONDAY = date(2026, 10, 26)


def repeat(start: date = MONDAY, kind: str = "days", **rule) -> Repeat:
    return Repeat(text="a", position="V", start=start, **clean_rule(kind, **rule))


def matching(r: Repeat, first: date, days: int) -> list[date]:
    return [d for d in (first + timedelta(n) for n in range(days)) if occurs_on(r, d)]


def test_daily_from_start():
    assert matching(repeat(kind="days"), MONDAY - timedelta(2), 5) == [MONDAY + timedelta(n) for n in range(3)]


def test_every_n_days_counts_from_start():
    r = repeat(kind="days", interval=3)
    assert matching(r, MONDAY - timedelta(3), 10) == [MONDAY, MONDAY + timedelta(3), MONDAY + timedelta(6)]


def test_weekdays():
    r = repeat(kind="weekdays", weekdays=[0, 4])
    assert matching(r, MONDAY - timedelta(7), 14) == [MONDAY - timedelta(7), MONDAY - timedelta(3), MONDAY, MONDAY + timedelta(4)]


def test_monthdays():
    r = repeat(kind="monthdays", monthdays=[1, 15])
    assert matching(r, date(2026, 1, 1), 31) == [date(2026, 1, 1), date(2026, 1, 15)]


@pytest.mark.parametrize(
    "monthdays, month, expected",
    [
        ([31], date(2026, 2, 1), [date(2026, 2, 28)]),
        ([30], date(2028, 2, 1), [date(2028, 2, 29)]),
        ([31], date(2026, 4, 1), [date(2026, 4, 30)]),
        ([30, 31], date(2026, 2, 1), [date(2026, 2, 28)]),
        ([31], date(2026, 1, 1), [date(2026, 1, 31)]),
    ],
)
def test_monthdays_past_month_end_fall_on_its_last_day(monthdays, month, expected):
    days = (month.replace(month=month.month + 1) - month).days
    assert matching(repeat(kind="monthdays", monthdays=monthdays), month, days) == expected


def test_unknown_kind_never_occurs():
    r = repeat()
    r.kind = "later"
    assert not occurs_on(r, MONDAY)


def test_clean_rule_sorts_dedupes_and_resets_unused_fields():
    assert clean_rule("weekdays", interval=3, weekdays=[4, 0, 4], monthdays=[1]) == {
        "kind": "weekdays",
        "interval": 1,
        "weekdays": [0, 4],
        "monthdays": [],
    }
    assert clean_rule("monthdays", monthdays=(31, 1)) == {"kind": "monthdays", "interval": 1, "weekdays": [], "monthdays": [1, 31]}
    assert clean_rule("days", interval=2, weekdays=[1]) == {"kind": "days", "interval": 2, "weekdays": [], "monthdays": []}
    assert clean_rule("days")["interval"] == 1


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "hours"},
        {"kind": "days", "interval": 0},
        {"kind": "days", "interval": -1},
        {"kind": "days", "interval": 1.5},
        {"kind": "days", "interval": True},
        {"kind": "days", "interval": "2"},
        {"kind": "weekdays"},
        {"kind": "weekdays", "weekdays": []},
        {"kind": "weekdays", "weekdays": [7]},
        {"kind": "weekdays", "weekdays": [-1]},
        {"kind": "monthdays", "monthdays": [0]},
        {"kind": "monthdays", "monthdays": [32]},
        {"kind": "monthdays"},
    ],
)
def test_clean_rule_rejects_invalid_values(rule):
    with pytest.raises(ValueError):
        clean_rule(**rule)


@pytest.mark.parametrize("weekdays", [1, "1", [1.0], [True], [None]])
def test_clean_rule_rejects_non_integer_lists(weekdays):
    with pytest.raises(TypeError):
        clean_rule("weekdays", weekdays=weekdays)
