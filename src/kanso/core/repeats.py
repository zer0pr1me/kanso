"""Repeat rules: every `interval` days from `start`, on `weekdays` (0 is Monday), or on `monthdays`."""

import calendar
from datetime import date

from kanso.core.models import Repeat

KINDS = ("days", "weekdays", "monthdays")
RULE_FIELDS = {"kind", "interval", "weekdays", "monthdays"}


def numbers(values: list[int], lo: int, hi: int, what: str) -> list[int]:
    """Sorted without repeats; non-empty and each within `lo`-`hi`."""
    if isinstance(values, (str, int)) or not all(type(v) is int for v in values):
        raise TypeError(f"{what} must be a list of whole numbers")
    if not values:
        raise ValueError(f"Choose at least one of {what.lower()}")
    if not all(lo <= v <= hi for v in values):
        raise ValueError(f"{what} must be within {lo}-{hi}")
    return sorted(set(values))


def clean_rule(kind: str, interval: int = 1, weekdays: list[int] = (), monthdays: list[int] = ()) -> dict:
    """The rule as model fields, validated; fields its kind doesn't use are reset."""
    rule = {"kind": kind, "interval": 1, "weekdays": [], "monthdays": []}
    match kind:
        case "days":
            if type(interval) is not int or interval < 1:
                raise ValueError("Interval must be a whole number of days, at least 1")
            rule["interval"] = interval
        case "weekdays":
            rule["weekdays"] = numbers(weekdays, 0, 6, "Weekdays")
        case "monthdays":
            rule["monthdays"] = numbers(monthdays, 1, 31, "Days of month")
        case _:
            raise ValueError(f"Unknown repeat kind {kind!r}")
    return rule


def occurs_on(repeat: Repeat, day: date) -> bool:
    """Whether `day` matches; month days past a month's end fall on its last day."""
    match repeat.kind:
        case "days":
            return day >= repeat.start and (day - repeat.start).days % repeat.interval == 0
        case "weekdays":
            return day.weekday() in repeat.weekdays
        case "monthdays":
            last = calendar.monthrange(day.year, day.month)[1]
            return day.day in {min(d, last) for d in repeat.monthdays}
    return False
