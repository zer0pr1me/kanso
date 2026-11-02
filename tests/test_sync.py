"""Replicas converging through digests, exported rows and merges, with no network."""

import json
from datetime import date, timedelta

import pytest

from kanso.core.migrations import current_version
from kanso.core.store import Store
from kanso.core.sync import wanted

TOMORROW = date.today() + timedelta(days=1)


@pytest.fixture
def stores(tmp_path):
    a, b = Store(tmp_path / "a.db"), Store(tmp_path / "b.db")
    yield a, b
    a.close()
    b.close()


def sync(a: Store, b: Store) -> tuple[int, int]:
    """One exchange both ways; how many rows changed on a and on b."""
    da, db = a.digest(), b.digest()
    to_b, to_a = a.export(wanted(da, db)), b.export(wanted(db, da))
    return a.merge_rows(to_a, current_version()), b.merge_rows(to_b, current_version())


def contents(store: Store) -> list:
    return [
        (l.name, [(t.title, t.done, t.labels) for t in store.todos(l.id)], [r.text for r in store.repeats(l.id)])
        for l in store.lists()
    ] + [d.key for d in store.devices()]


def test_wanted_is_what_the_other_side_lacks_or_has_otherwise():
    mine = {"todo": {"1": "h1", "2": "h2", "3": "h3"}, "device": {}}
    theirs = {"todo": {"1": "h1", "2": "other"}}
    assert wanted(mine, theirs) == {"todo": ["2", "3"]}


def test_replicas_converge_and_a_second_sync_changes_nothing(stores):
    a, b = stores
    inbox = a.add_list("Inbox")
    a.add_todo(inbox.id, "milk", labels=["errands"])
    a.add_repeat(inbox.id, "water")
    a.add_device("key-a", "Laptop")
    work = b.add_list("Work")
    b.add_todo(work.id, "report")
    assert sync(a, b) == (2, 5)  # list, todo / list, todo, repeat, its todo, device
    assert contents(a) == contents(b)
    assert a.digest() == b.digest()
    assert sync(a, b) == (0, 0)


def test_concurrent_edits_of_different_fields_both_survive(stores):
    a, b = stores
    todo = a.add_todo(a.add_list("Inbox").id, "milk")
    sync(a, b)
    a.update_todo(todo.id, text="oat milk")
    b.update_todo(todo.id, done=True)
    sync(a, b)
    assert [(t.text, t.done, t.completed_at) for t in b.todos(todo.list_id)] == [("oat milk", True, date.today())]
    assert contents(a) == contents(b)


def test_same_field_last_writer_wins_and_deletes_propagate(stores):
    a, b = stores
    todo = a.add_todo(a.add_list("Inbox").id, "milk")
    keep = a.add_todo(todo.list_id, "bread")
    sync(a, b)
    a.update_todo(todo.id, text="first")
    b.update_todo(todo.id, text="second")
    b.delete_todo(keep.id)
    sync(a, b)
    assert [t.text for t in a.todos(todo.list_id)] == ["second"]
    assert contents(a) == contents(b)


def test_loaded_objects_see_merged_values(stores):
    a, b = stores
    todo = a.add_todo(a.add_list("Inbox").id, "milk")
    sync(a, b)
    [loaded] = b.todos(todo.list_id)
    a.update_todo(todo.id, text="oat milk")
    sync(a, b)
    assert loaded.text == "oat milk"


def test_repeat_renames_retitle_its_todos_on_every_replica(stores):
    a, b = stores
    inbox = a.add_list("Inbox")
    repeat = a.add_repeat(inbox.id, "water")
    sync(a, b)
    a.update_repeat(repeat.id, text="water plants")
    b.spawn(TOMORROW)  # concurrently spawned with the old text
    assert sync(a, b) == (1, 1)  # b's new todo / a's repeat
    assert contents(a) == contents(b)
    assert [t.title for t in b.todos(inbox.id)] == ["water plants"] * 2
    assert a.digest() == b.digest()


def test_repeating_todo_arriving_before_its_repeat_shows_its_own_text(stores):
    a, b = stores
    inbox = a.add_list("Inbox")
    repeat = a.add_repeat(inbox.id, "water")
    a.update_repeat(repeat.id, text="water plants")
    rows = a.export(wanted(a.digest(), b.digest()))
    b.merge_rows({k: v for k, v in rows.items() if k != "repeat"}, current_version())
    assert [t.title for t in b.todos(inbox.id)] == ["water"]
    sync(a, b)
    assert [t.title for t in b.todos(inbox.id)] == ["water plants"]


def test_a_todo_made_ordinary_stays_so_on_every_replica(stores):
    a, b = stores
    inbox = a.add_list("Inbox")
    repeat = a.add_repeat(inbox.id, "water")
    a.delete_repeat(repeat.id)
    [todo] = a.todos(inbox.id)
    a.update_todo(todo.id, text="water once more")
    sync(a, b)
    assert [(t.title, t.repeat_id) for t in b.todos(inbox.id)] == [("water once more", None)]


def test_merging_moves_the_clock_past_remote_stamps(stores):
    a, b = stores
    a.add_list("Inbox")
    remote = b.add_list("Work")
    a.clock.millis = 0
    sync(a, b)
    assert a.clock.now() > remote.modified_at


def test_rows_from_an_older_schema_are_upgraded(stores):
    a, b = stores
    todo = a.add_todo(a.add_list("Inbox").id, "milk")
    rows = a.export(wanted(a.digest(), {}))
    for row in rows["todo"] + rows["todolist"]:
        del row["stamps"]  # as schema 2 sent them, before field stamps
    assert b.merge_rows(rows, 2) == 2
    [copy] = b.todos(todo.list_id)
    assert copy.text == "milk" and set(copy.stamps.values()) == {todo.modified_at}


def rows_of(store: Store) -> dict:
    return store.export(wanted(store.digest(), {}))


@pytest.mark.parametrize(
    "spoil, error",
    [
        (lambda rows: rows.update(meta=[{"key": "node", "value": "x"}]), "unknown tables"),
        (lambda rows: rows["todolist"][0].update(extra=1), "columns"),
        (lambda rows: rows["todolist"][0].pop("name"), "columns"),
        (lambda rows: rows["todolist"][0].update(stamps='["x"]'), "malformed stamps"),
        (lambda rows: rows["todolist"][0].update(stamps=json.dumps({"name": f"{10**14:015d}-000000-x"})), "ahead"),
    ],
)
def test_bad_rows_are_refused_and_nothing_is_written(stores, spoil, error):
    a, b = stores
    a.add_list("Inbox")
    a.add_list("Work")
    rows = rows_of(a)
    rows["todolist"].reverse()  # the good row first, so refusing must undo it
    spoil(rows)
    with pytest.raises(ValueError, match=error):
        b.merge_rows(rows, current_version())
    assert b.lists() == []


def test_rows_from_a_newer_schema_are_refused(stores):
    a, b = stores
    a.add_list("Inbox")
    with pytest.raises(ValueError, match="newer"):
        b.merge_rows(rows_of(a), current_version() + 1)


def test_habits_and_their_days_converge(stores):
    a, b = stores
    habit = a.add_habit(a.add_list("Inbox").id, "Read")
    habit.start = date.today() - timedelta(days=1)
    a._save(habit)
    a.spawn()
    sync(a, b)
    yesterday = b.habit_days(habit.list_id)[-1]
    b.mark_habit_day(yesterday.id, "success")
    a.update_habit(habit.id, text="Read more")
    b.spawn()  # spawns nothing new: same ids as a's
    sync(a, b)
    for store in (a, b):
        assert [(d.title, d.state) for d in store.habit_days(habit.list_id)] == [("Read more", None), ("Read more", "success")]
    assert a.digest() == b.digest()
