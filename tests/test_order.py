from datetime import date

from kanso.core.models import Todo
from kanso.core.order import done_before, sections

EARLY, LATE = date(2026, 1, 1), date(2026, 1, 2)


def todo(text: str, day: date | None, done: bool = False) -> Todo:
    return Todo(text=text, position="V", scheduled_at=day, done=done)


def test_sections_by_date_unscheduled_last_each_autosorted_done_first():
    todos = [
        todo("a", LATE),
        todo("b", None),
        todo("c", EARLY),
        todo("d", LATE, done=True),
        todo("e", None, done=True),
        todo("f", LATE),
    ]
    grouped = [(day, [t.text for t in group]) for day, group in sections(todos)]
    assert grouped == [(EARLY, ["c"]), (LATE, ["d", "a", "f"]), (None, ["e", "b"])]


def test_no_sections_without_todos():
    assert sections([]) == []


def test_done_before_a_day_needs_done_on_an_earlier_day():
    def done(on: date | None) -> Todo:
        return Todo(text="t", position="V", done=True, completed_at=on)

    assert done_before(done(EARLY), LATE)
    assert not done_before(done(LATE), LATE)
    assert not done_before(done(None), LATE)  # as rows from a replica may be
    assert not done_before(Todo(text="t", position="V", completed_at=EARLY), LATE)
