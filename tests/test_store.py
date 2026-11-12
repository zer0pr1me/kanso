from datetime import date, timedelta
from uuid import uuid4, uuid5

import pytest
from sqlmodel import select

from conftest import texts
from kanso.core import store as store_module
from kanso.core.models import SPAWNED, SYNCED_MODELS, Habit, HabitDay, Repeat, Todo, TodoList, device_id
from kanso.core.store import NotFound, Store

# Setup


@pytest.mark.parametrize("env", [None, ""])
def test_default_path_is_user_data_dir(tmp_path, monkeypatch, env):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    if env is None:
        monkeypatch.delenv("KANSO_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("KANSO_DATA_DIR", env)
    assert store_module.default_path() == tmp_path / "kanso" / "kanso.db"
    assert (tmp_path / "kanso").is_dir()


def test_default_path_from_env_creates_dir_and_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KANSO_DATA_DIR", "~/a/b")
    assert store_module.default_path() == tmp_path / "a" / "b" / "kanso.db"
    assert (tmp_path / "a" / "b").is_dir()


def test_store_opens_database_in_env_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KANSO_DATA_DIR", str(tmp_path / "data"))
    Store().close()
    assert (tmp_path / "data" / "kanso.db").is_file()


def test_every_field_of_every_synced_model_persists(path):
    s = Store(path)
    todo_list = s.add_list("Inbox")
    s.add_repeat(todo_list.id, "r", "weekdays", weekdays=[date.today().weekday()])
    s.update_todo(s.todos(todo_list.id)[0].id, done=True, labels=["home"], scheduled_at=None)
    s.mark_habit_day(started_days_ago(s, todo_list, "h", 1).id, "success")
    s.add_device("k", "Laptop")
    s.introduce("k", "j", "signature")
    before = {m: [r.model_dump() for r in s.db.exec(select(m))] for m in SYNCED_MODELS}
    s.close()
    s = Store(path)
    assert {m: [r.model_dump() for r in s.db.exec(select(m))] for m in SYNCED_MODELS} == before
    s.close()


@pytest.mark.parametrize("model", SYNCED_MODELS)
def test_clock_resumes_after_stored_stamps(path, model):
    s = Store(path)
    todo_list = s.add_list("Inbox")
    s.add_repeat(todo_list.id, "r")
    s.add_habit(todo_list.id, "h")
    s.add_device("k", "Laptop")
    s.introduce("k", "j", "signature")
    latest = s.db.exec(select(model).order_by(model.modified_at.desc())).first()
    # Make this model's stamp the newest, as if written by a replica with a clock ahead.
    latest.modified_at = "999999999999999-000000-z"
    s.db.commit()
    s.close()
    s = Store(path)
    assert s.clock.now() > latest.modified_at
    s.close()


def test_every_write_stamps_monotonically(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    first = todo.modified_at
    assert first > todo_list.modified_at
    assert store.update_todo(todo.id, text="b").modified_at > first


def test_new_records_stamp_every_field_at_once(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    for record in (todo_list, store.add_repeat(todo_list.id, "r"), store.add_habit(todo_list.id, "h"), todo):
        fields = record.model_dump().keys() - {"id", "modified_at", "stamps"}
        assert record.stamps == dict.fromkeys(fields, record.modified_at)


def test_spawned_instances_are_stamped_older_than_any_write_but_where_they_differ(store, todo_list):
    store.add_repeat(todo_list.id, "r")
    store.add_habit(todo_list.id, "h")
    [todo], [day] = store.todos(todo_list.id), store.habit_days(todo_list.id)
    for record in (todo, day):
        fields = record.model_dump().keys() - {"id", "modified_at", "stamps"}
        local = {"text", "position", "created_at"} & fields
        assert record.stamps == dict.fromkeys(fields - local, SPAWNED) | dict.fromkeys(local, record.modified_at)
        assert record.modified_at > todo_list.modified_at > SPAWNED
    store.update_todo(todo.id, done=True)
    assert todo.stamps["done"] == todo.stamps["completed_at"] == todo.modified_at


def test_updates_stamp_only_changed_fields(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    created = todo.modified_at
    store.update_todo(todo.id, text="a", done=True)
    assert todo.modified_at > created
    changed = {field for field, stamp in todo.stamps.items() if stamp > created}
    assert changed == {"done", "completed_at"}
    assert todo.stamps["done"] == todo.modified_at


def test_unchanged_update_writes_nothing(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", labels=["x"])
    repeat = store.add_repeat(todo_list.id, "r")
    before = [(r.modified_at, dict(r.stamps)) for r in (todo, repeat)]
    store.update_todo(todo.id, text=" a ", labels=["#x"])
    store.update_repeat(repeat.id, text="r", kind="days", interval=1)
    assert [(r.modified_at, r.stamps) for r in (todo, repeat)] == before


def test_writes_stamp_once(store, todo_list, monkeypatch):
    stamps = []
    now = store.clock.now
    monkeypatch.setattr(store.clock, "now", lambda: stamps.append(now()) or stamps[-1])
    todo = store.add_todo(todo_list.id, "a", done=True, labels=["x"])
    assert stamps == [todo.modified_at]


# Not found


def deleted_records(store):
    todo_list = store.add_list("Inbox")
    todo = store.add_todo(todo_list.id, "a")
    repeat = store.add_repeat(todo_list.id, "r")
    habit_day = started_days_ago(store, todo_list, "h", 1)
    habit = store.db.get(Habit, habit_day.habit_id)
    store.delete_todo(todo.id)
    store.delete_repeat(repeat.id)
    store.delete_habit(habit.id)
    store.delete_habit_day(habit_day.id)
    store.delete_list(todo_list.id)
    return {TodoList: todo_list.id, Todo: todo.id, Repeat: repeat.id, Habit: habit.id, HabitDay: habit_day.id}


OPERATIONS = {
    TodoList: [
        lambda s, id: s.rename_list(id, "x"),
        lambda s, id: s.delete_list(id),
        lambda s, id: s.todos(id),
        lambda s, id: s.repeats(id),
        lambda s, id: s.add_todo(id, "x"),
        lambda s, id: s.add_repeat(id, "x"),
        lambda s, id: s.move_list(id, 0),
        lambda s, id: s.habits(id),
        lambda s, id: s.habit_days(id),
        lambda s, id: s.add_habit(id, "x"),
    ],
    Todo: [
        lambda s, id: s.update_todo(id, text="x"),
        lambda s, id: s.duplicate_todo(id),
        lambda s, id: s.shift_todo(id, 1),
        lambda s, id: s.move_todo(id, 0),
        lambda s, id: s.delete_todo(id),
        lambda s, id: s.repeat_of(id),
    ],
    Repeat: [
        lambda s, id: s.update_repeat(id, text="x"),
        lambda s, id: s.move_repeat(id, 0),
        lambda s, id: s.delete_repeat(id),
    ],
    Habit: [
        lambda s, id: s.update_habit(id, text="x"),
        lambda s, id: s.move_habit(id, 0),
        lambda s, id: s.delete_habit(id),
    ],
    HabitDay: [
        lambda s, id: s.mark_habit_day(id, "success"),
        lambda s, id: s.delete_habit_day(id),
        lambda s, id: s.habit_of(id),
    ],
}


@pytest.mark.parametrize("model, op", [(m, op) for m, ops in OPERATIONS.items() for op in ops])
def test_missing_or_deleted_records_not_found(store, model, op):
    for id in (deleted_records(store)[model], uuid4()):
        with pytest.raises(NotFound):
            op(store, id)


# Lists


def test_lists_keep_creation_order(store):
    for name in ["b", "a", "c"]:
        store.add_list(name)
    assert [l.name for l in store.lists()] == ["b", "a", "c"]


def test_add_and_rename_list_strip_name(store):
    todo_list = store.add_list("  Work  ")
    store.rename_list(todo_list.id, " Job ")
    assert [l.name for l in store.lists()] == ["Job"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_list_name_validation(store, todo_list, name):
    with pytest.raises(ValueError):
        store.add_list(name)
    with pytest.raises(ValueError):
        store.rename_list(todo_list.id, name)
    assert [l.name for l in store.lists()] == ["Inbox"]


def test_delete_list_tombstones_only_it_and_hides_its_contents(store, todo_list):
    other = store.add_list("Other")
    todo = store.add_todo(todo_list.id, "a", labels=["gone"])
    store.add_repeat(todo_list.id, "r")
    kept = store.add_todo(other.id, "b")
    store.delete_list(todo_list.id)
    assert [l.name for l in store.lists()] == ["Other"]
    assert store.todos(other.id) == [kept]
    assert store.db.get(TodoList, todo_list.id).deleted
    assert not store.db.get(Todo, todo.id).deleted
    assert store.labels() == []
    assert store.spawn(date.today() + timedelta(days=1)) == []


def test_records_of_a_list_never_received_are_hidden(store, todo_list):
    """As when sync delivers a todo or repeat before its list."""
    store._save(
        Todo(list_id=uuid4(), text="a", position="V", labels=["orphan"]),
        Repeat(list_id=uuid4(), text="r", position="V"),
    )
    assert store.labels() == []
    assert store.spawn() == []


# Todos


def test_add_todo_defaults(store, todo_list):
    todo = store.add_todo(todo_list.id, " a ")
    today = date.today()
    assert (todo.text, todo.done, todo.created_at, todo.scheduled_at, todo.completed_at, todo.labels, todo.repeat_id) == (
        "a",
        False,
        today,
        today,
        None,
        [],
        None,
    )


def test_add_todo_with_fields(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", done=True, scheduled_at=None)
    assert (todo.done, todo.completed_at, todo.scheduled_at) == (True, date.today(), None)


def test_add_todo_appends_or_inserts_at_index(store, todo_list):
    for text in ["a", "c"]:
        store.add_todo(todo_list.id, text)
    store.add_todo(todo_list.id, "b", index=1)
    store.add_todo(todo_list.id, "start", index=0)
    store.add_todo(todo_list.id, "end", index=99)
    store.add_todo(todo_list.id, "clamped", index=-5)
    assert texts(store, todo_list.id) == ["clamped", "start", "a", "b", "c", "end"]


def test_todos_are_per_list(store, todo_list):
    other = store.add_list("Other")
    store.add_todo(todo_list.id, "a")
    store.add_todo(other.id, "b")
    assert texts(store, todo_list.id) == ["a"]
    assert texts(store, other.id) == ["b"]


def test_update_text_and_schedule(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", scheduled_at=date(2026, 1, 1))
    store.update_todo(todo.id, text=" b ", scheduled_at=None)
    [todo] = store.todos(todo_list.id)
    assert (todo.text, todo.scheduled_at) == ("b", None)


@pytest.mark.parametrize("text", ["", "   "])
def test_todo_text_must_not_be_blank(store, todo_list, text):
    todo = store.add_todo(todo_list.id, "a")
    with pytest.raises(ValueError):
        store.add_todo(todo_list.id, text)
    with pytest.raises(ValueError):
        store.update_todo(todo.id, text=text, done=True)
    assert [(t.text, t.done) for t in store.todos(todo_list.id)] == [("a", False)]


def test_toggling_done_sets_and_clears_completed_at(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    assert store.update_todo(todo.id, done=True).completed_at == date.today()
    todo.completed_at = date(2026, 1, 1)
    assert store.update_todo(todo.id, done=True, text="b").completed_at == date(2026, 1, 1)
    assert store.update_todo(todo.id, done=False).completed_at is None


@pytest.mark.parametrize("field", ["id", "list_id", "position", "deleted", "completed_at", "repeat_id", "bogus"])
def test_todo_rejects_other_fields(store, todo_list, field):
    todo = store.add_todo(todo_list.id, "a")
    with pytest.raises(TypeError):
        store.update_todo(todo.id, **{field: None})
    with pytest.raises(TypeError):
        store.add_todo(todo_list.id, "b", **{field: None})
    assert texts(store, todo_list.id) == ["a"]


@pytest.mark.parametrize("src, index, expected", [(2, 0, "cab"), (2, 1, "acb"), (2, 99, "abc"), (2, -1, "cab"), (0, 1, "bac")])
def test_move_todo(store, todo_list, src, index, expected):
    todos = [store.add_todo(todo_list.id, t) for t in "abc"]
    store.move_todo(todos[src].id, index)
    assert "".join(texts(store, todo_list.id)) == expected


@pytest.mark.parametrize("src, index, expected", [(2, 0, "cab"), (0, 1, "bac"), (0, 99, "bca")])
def test_move_list_and_repeat(store, todo_list, src, index, expected):
    lists = [todo_list, *(store.add_list(n) for n in "bc")]
    store.rename_list(todo_list.id, "a")
    store.move_list(lists[src].id, index)
    assert "".join(l.name for l in store.lists()) == expected
    other = store.add_list("Other")
    repeats = [store.add_repeat(todo_list.id, t) for t in "abc"]
    store.add_repeat(other.id, "x")
    store.move_repeat(repeats[src].id, index)
    assert "".join(r.text for r in store.repeats(todo_list.id)) == expected


def test_done_todos_go_last_and_reopened_ones_first(store, todo_list):
    a, b, c = [store.add_todo(todo_list.id, t) for t in "abc"]
    store.update_todo(a.id, done=True)
    assert "".join(texts(store, todo_list.id)) == "bca"
    store.update_todo(c.id, done=True)
    assert "".join(texts(store, todo_list.id)) == "bac"
    store.update_todo(a.id, done=False)
    assert "".join(texts(store, todo_list.id)) == "abc"
    position = b.position
    store.update_todo(b.id, done=False, text="b2")  # not toggled: stays put
    assert b.position == position


def tie(store, todo_list) -> list[str]:
    """Todos a-d where b, c and d share a's key, as replicas adding at the same spot can;
    the order they show in, which ties break by id."""
    todos = [store.add_todo(todo_list.id, t) for t in "abcd"]
    for todo in todos[1:]:
        todo.position = todos[0].position
    store._save(*todos[1:])
    return texts(store, todo_list.id)


@pytest.mark.parametrize("index", [1, 2, 3])
def test_inserting_between_tied_todos_rekeys_them_in_order(store, todo_list, index):
    order = tie(store, todo_list)
    store.add_todo(todo_list.id, "new", index=index)
    assert texts(store, todo_list.id) == order[:index] + ["new"] + order[index:]
    positions = [t.position for t in store.todos(todo_list.id)][index - 1 :]
    assert positions == sorted(set(positions))  # ties before the gap may stay; from there on, strictly after


def test_moving_between_tied_todos(store, todo_list):
    order = tie(store, todo_list)
    last = store.todos(todo_list.id)[-1]
    store.move_todo(last.id, 1)
    assert texts(store, todo_list.id) == [order[0], order[3], order[1], order[2]]


def test_delete_todo_tombstones_it(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    store.add_todo(todo_list.id, "b")
    store.delete_todo(todo.id)
    assert texts(store, todo_list.id) == ["b"]
    assert store.db.get(Todo, todo.id).deleted


def test_duplicate_todo_copies_below_it_not_done(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", scheduled_at=date(2026, 1, 1), labels=["home", "work"], done=True)
    store.add_todo(todo_list.id, "b")
    copy = store.duplicate_todo(todo.id)
    assert copy.id != todo.id
    assert (copy.text, copy.labels, copy.scheduled_at, copy.done, copy.completed_at) == (
        "a",
        ["home", "work"],
        date(2026, 1, 1),
        False,
        None,
    )
    assert texts(store, todo_list.id) == ["a", "a", "b"]
    assert store.todos(todo_list.id)[1] == copy


@pytest.mark.parametrize("delete_repeat", [False, True])
def test_repeating_todos_cannot_be_duplicated(store, todo_list, delete_repeat):
    repeat = store.add_repeat(todo_list.id, "r")
    if delete_repeat:
        store.delete_repeat(repeat.id)
    [todo] = store.todos(todo_list.id)
    with pytest.raises(ValueError):
        store.duplicate_todo(todo.id)
    assert texts(store, todo_list.id) == ["r"]


@pytest.mark.parametrize("days", [1, -1, 7, 0])
def test_shift_todo(store, todo_list, days):
    todo = store.add_todo(todo_list.id, "a", scheduled_at=date(2026, 1, 31))
    assert store.shift_todo(todo.id, days).scheduled_at == date(2026, 1, 31) + timedelta(days=days)


def test_shift_unscheduled_todo_counts_from_today(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", scheduled_at=None)
    assert store.shift_todo(todo.id, 1).scheduled_at == date.today() + timedelta(days=1)


# Labels


def test_labels_are_cleaned(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", labels=[" home ", "#work", "", "#", "home", "Home", "x" * 50])
    assert todo.labels == ["home", "work", "Home", "x" * 50]
    assert store.update_todo(todo.id, labels=[]).labels == []


@pytest.mark.parametrize("labels", [["two words"], ["tab\tbed"], ["x" * 51]])
def test_label_validation(store, todo_list, labels):
    todo = store.add_todo(todo_list.id, "a", labels=["home"])
    with pytest.raises(ValueError):
        store.update_todo(todo.id, labels=labels)
    with pytest.raises(ValueError):
        store.add_todo(todo_list.id, "b", labels=labels)
    assert [t.labels for t in store.todos(todo_list.id)] == [["home"]]


def test_labels_must_be_a_list(store, todo_list):
    with pytest.raises(TypeError):
        store.add_todo(todo_list.id, "a", labels="home")


def test_labels_lists_live_labels_sorted(store, todo_list):
    other = store.add_list("Other")
    store.add_todo(todo_list.id, "a", labels=["work", "Home"])
    store.add_todo(other.id, "b", labels=["errands", "work"])
    store.delete_todo(store.add_todo(todo_list.id, "c", labels=["deleted"]).id)
    assert store.labels() == ["errands", "Home", "work"]


# Repeats


def test_add_repeat_defaults_to_daily_from_today(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "  Water plants ")
    assert (repeat.text, repeat.kind, repeat.interval, repeat.weekdays, repeat.monthdays, repeat.start) == (
        "Water plants",
        "days",
        1,
        [],
        [],
        date.today(),
    )
    assert store.repeats(todo_list.id) == [repeat]


def test_repeats_keep_order_and_are_per_list(store, todo_list):
    other = store.add_list("Other")
    for text in "bac":
        store.add_repeat(todo_list.id, text, "weekdays", weekdays=[4, 0])
    store.add_repeat(other.id, "x")
    assert [(r.text, r.weekdays) for r in store.repeats(todo_list.id)] == [("b", [0, 4]), ("a", [0, 4]), ("c", [0, 4])]
    assert [r.text for r in store.repeats(other.id)] == ["x"]


@pytest.mark.parametrize("text", ["", "   "])
def test_repeat_text_must_not_be_blank(store, todo_list, text):
    repeat = store.add_repeat(todo_list.id, "a")
    with pytest.raises(ValueError):
        store.add_repeat(todo_list.id, text)
    with pytest.raises(ValueError):
        store.update_repeat(repeat.id, text=text)
    assert [r.text for r in store.repeats(todo_list.id)] == ["a"]


def test_invalid_repeat_changes_are_rejected_unchanged(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a", "days", interval=2)
    with pytest.raises(ValueError):
        store.add_repeat(todo_list.id, "b", "weekdays")
    with pytest.raises(ValueError):
        store.update_repeat(repeat.id, text="b", kind="weekdays")
    with pytest.raises(TypeError):
        store.update_repeat(repeat.id, text="b", start=date(2020, 1, 1))
    with pytest.raises(TypeError):
        store.add_repeat(todo_list.id, "b", deleted=True)
    assert [(r.text, r.kind, r.interval) for r in store.repeats(todo_list.id)] == [("a", "days", 2)]


def test_changing_only_text_keeps_rule_and_start(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a", "days", interval=3)
    repeat.start = date(2026, 1, 1)
    store.update_repeat(repeat.id, text=" b ", interval=3)
    assert (repeat.text, repeat.interval, repeat.start) == ("b", 3, date(2026, 1, 1))


def test_changing_rule_restarts_it_today(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a", "days", interval=3)
    repeat.start = date(2026, 1, 1)
    store.update_repeat(repeat.id, kind="weekdays", weekdays=[2])
    assert (repeat.kind, repeat.interval, repeat.weekdays, repeat.start) == ("weekdays", 1, [2], date.today())


def test_renaming_a_repeat_retitles_its_todos_without_writing_them(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a")
    store.add_repeat(todo_list.id, "o")
    store.spawn(date.today() + timedelta(days=1))
    store.update_todo(store.todos(todo_list.id)[0].id, done=True)  # moves it last
    before = [(t.text, t.modified_at, dict(t.stamps)) for t in store.todos(todo_list.id)]
    store.update_repeat(repeat.id, text="b")
    assert texts(store, todo_list.id) == ["o", "b", "o", "b"]
    assert [(t.text, t.modified_at, t.stamps) for t in store.todos(todo_list.id)] == before


def test_repeating_todo_text_is_set_by_its_live_repeat_only(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a")
    [todo] = store.todos(todo_list.id)
    assert store.repeat_of(todo.id) == repeat
    with pytest.raises(ValueError):
        store.update_todo(todo.id, text="b", done=True)
    assert (todo.title, todo.done, todo.repeat_id) == ("a", False, repeat.id)
    store.update_todo(todo.id, done=True, labels=["x"], scheduled_at=None)
    assert (todo.done, todo.labels, todo.scheduled_at, todo.repeat_id) == (True, ["x"], None, repeat.id)


def test_todo_of_a_deleted_repeat_keeps_its_last_title_until_edited(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a")
    [todo] = store.todos(todo_list.id)
    store.update_repeat(repeat.id, text="b")
    store.delete_repeat(repeat.id)
    assert (store.repeat_of(todo.id), todo.title) == (None, "b")
    with pytest.raises(ValueError):
        store.update_todo(todo.id, text=" ")
    assert todo.repeat_id == repeat.id
    store.update_todo(todo.id, text="c")
    assert (todo.title, todo.repeat_id) == ("c", None)
    assert todo.stamps["repeat_id"] == todo.stamps["text"] == todo.modified_at


def test_todo_of_a_missing_repeat_shows_its_own_text(store, todo_list):
    """As when sync delivers a repeating todo before its repeat."""
    todo = store._save(Todo(list_id=todo_list.id, repeat_id=uuid4(), text="a", position="V"))
    assert (store.repeat_of(todo.id), todo.title) == (None, "a")
    store.update_todo(todo.id, text="b")
    assert (todo.title, todo.repeat_id) == ("b", None)


def test_ordinary_todo_title_is_its_text(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    assert (store.repeat_of(todo.id), todo.title) == (None, "a")
    assert Todo(list_id=todo_list.id, repeat_id=uuid4(), text="t", position="V").title == "t"  # outside a session


def test_delete_repeat_tombstones_it_keeping_its_todos(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a")
    store.delete_repeat(repeat.id)
    assert store.repeats(todo_list.id) == []
    assert store.db.get(Repeat, repeat.id).deleted
    assert texts(store, todo_list.id) == ["a"]
    assert store.spawn(date.today() + timedelta(days=1)) == []


# Spawning repeats

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


def test_adding_a_repeat_spawns_todays_todo_if_due(store, todo_list):
    store.add_todo(todo_list.id, "first", scheduled_at=None)
    repeat = store.add_repeat(todo_list.id, "a")
    store.add_repeat(todo_list.id, "later", "weekdays", weekdays=[TOMORROW.weekday()])
    [todo] = store.todos(todo_list.id)[1:]
    assert (todo.text, todo.repeat_id, todo.scheduled_at) == ("a", repeat.id, TODAY)
    assert todo.id == uuid5(repeat.id, TODAY.isoformat())


def test_updating_a_repeat_spawns_todays_todo_if_now_due(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a", "weekdays", weekdays=[TOMORROW.weekday()])
    assert texts(store, todo_list.id) == []
    store.update_repeat(repeat.id, kind="days")
    assert texts(store, todo_list.id) == ["a"]


def test_spawn_only_matching_repeats_on_given_day(store, todo_list):
    other = store.add_list("Other")
    store.add_repeat(todo_list.id, "tomorrow", "weekdays", weekdays=[TOMORROW.weekday()])
    store.add_repeat(todo_list.id, "today", "weekdays", weekdays=[TODAY.weekday()])
    store.add_repeat(other.id, "daily")
    spawned = store.spawn(TOMORROW)
    assert sorted((t.text, t.scheduled_at) for t in spawned) == [("daily", TOMORROW), ("tomorrow", TOMORROW)]
    assert texts(store, todo_list.id) == ["today", "tomorrow"]


def test_spawn_puts_several_repeats_in_order(store, todo_list):
    for text in "abc":
        store.add_repeat(todo_list.id, text)
    store.spawn(TOMORROW)
    assert texts(store, todo_list.id) == ["a", "b", "c"] * 2


def test_spawn_is_idempotent_and_skips_changed_or_deleted_occurrences(store, todo_list):
    store.add_repeat(todo_list.id, "a")
    assert store.spawn() == []
    [todo] = store.todos(todo_list.id)
    store.update_todo(todo.id, done=True)
    store.shift_todo(todo.id, 1)
    assert store.spawn() == []
    store.delete_todo(todo.id)
    assert store.spawn() == []
    assert texts(store, todo_list.id) == []


# Devices and local settings


def test_add_device_trusts_its_key(store):
    laptop = store.add_device("key-1", " Laptop ")
    assert laptop.id == device_id("key-1") and laptop.name == "Laptop"
    assert store.is_trusted("key-1") and not store.is_trusted("key-2")
    store.add_device("key-2", "Phone")
    assert [d.name for d in store.devices()] == ["Laptop", "Phone"]


def test_introductions_are_recorded_once(store):
    store.introduce("key-2", "key-1", "first")
    store.introduce("key-2", "key-1", "second")
    store.introduce("key-1", "key-2", "back")
    assert sorted((i.by, i.key, i.signature) for i in store.introductions()) == [
        ("key-1", "key-2", "first"),
        ("key-2", "key-1", "back"),
    ]


def test_re_adding_a_device_renames_and_restores_it(store):
    device = store.add_device("key-1", "Laptop")
    device.deleted = True
    store._save(device)
    assert not store.is_trusted("key-1") and store.knows_device("key-1") and not store.knows_device("key-2")
    store.add_device("key-1", "Work laptop")
    assert [(d.name, d.key) for d in store.devices()] == [("Work laptop", "key-1")]


def test_meta_is_set_once_and_persists(path):
    s = Store(path)
    assert s.meta("x") is None
    assert s.meta("x", lambda: "first") == "first"
    assert s.meta("x", lambda: "second") == "first"
    s.close()
    s = Store(path)
    assert s.meta("x", lambda: "third") == "first"
    s.set_meta("x", "changed")
    assert s.meta("x") == "changed"
    s.set_meta("x", None)
    s.set_meta("absent", None)
    assert s.meta("x") is None
    s.close()


# Habits


def started_days_ago(store, todo_list, text: str, days: int, **rule) -> HabitDay:
    """A habit started `days` ago, backfilled; its oldest day."""
    habit = store.add_habit(todo_list.id, text, **rule)
    habit.start = date.today() - timedelta(days=days)
    store._save(habit)
    store.spawn()
    return store.habit_days(todo_list.id)[-1]


def days_of(store, todo_list) -> list[tuple[str, date, str | None]]:
    return [(d.title, d.day, d.state) for d in store.habit_days(todo_list.id)]


TODAY = date.today()


def test_add_habit_spawns_today(store, todo_list):
    habit = store.add_habit(todo_list.id, " Read ", "days", interval=2)
    assert (habit.text, habit.kind, habit.interval, habit.start) == ("Read", "days", 2, TODAY)
    assert store.habits(todo_list.id) == [habit]
    [day] = store.habit_days(todo_list.id)
    assert (day.title, day.day, day.state, day.habit_id) == ("Read", TODAY, None, habit.id)
    assert day.id == uuid5(habit.id, TODAY.isoformat())
    assert store.todos(todo_list.id) == []  # habits spawn days, not todos


def test_habits_backfill_their_matching_days_since_start(store, todo_list):
    started_days_ago(store, todo_list, "every other", 4, kind="days", interval=2)
    weekday = (TODAY - timedelta(days=1)).weekday()
    started_days_ago(store, todo_list, "weekly", 10, kind="weekdays", weekdays=[weekday])
    by_habit = {}
    for title, day, _ in days_of(store, todo_list):
        by_habit.setdefault(title, []).append((TODAY - day).days)
    assert by_habit["every other"] == [0, 2, 4]
    assert by_habit["weekly"] == [n for n in range(11) if (TODAY - timedelta(days=n)).weekday() == weekday]
    assert store.spawn() == []  # idempotent


def test_habit_days_are_newest_first_in_habit_order(store, todo_list):
    b = started_days_ago(store, todo_list, "b", 1)
    started_days_ago(store, todo_list, "a", 1)
    store.move_habit(b.habit_id, 1)
    assert [(t, (TODAY - d).days) for t, d, _ in days_of(store, todo_list)] == [("a", 0), ("b", 0), ("a", 1), ("b", 1)]


def test_marking_past_days_and_clearing(store, todo_list):
    day = started_days_ago(store, todo_list, "h", 1)
    assert store.mark_habit_day(day.id, "success").state == "success"
    assert store.mark_habit_day(day.id, "failure").state == "failure"
    assert store.mark_habit_day(day.id, None).state is None


def test_today_and_later_cant_be_marked(store, todo_list):
    store.add_habit(todo_list.id, "h")
    [today] = store.habit_days(todo_list.id)
    with pytest.raises(ValueError, match="once the day is over"):
        store.mark_habit_day(today.id, "success")
    assert today.state is None


@pytest.mark.parametrize("state", ["done", "", True])
def test_only_success_failure_or_unchecked(store, todo_list, state):
    day = started_days_ago(store, todo_list, "h", 1)
    with pytest.raises(ValueError, match="success, a failure or unchecked"):
        store.mark_habit_day(day.id, state)


def test_deleted_days_are_not_spawned_again(store, todo_list):
    day = started_days_ago(store, todo_list, "h", 2)
    store.delete_habit_day(day.id)
    assert store.spawn() == []
    assert [(TODAY - d).days for _, d, _ in days_of(store, todo_list)] == [0, 1]


def test_deleting_a_habit_keeps_its_days_titled(store, todo_list):
    day = started_days_ago(store, todo_list, "Read", 1)
    store.update_habit(day.habit_id, text="Read more")
    assert store.habit_of(day.id).text == "Read more"
    store.delete_habit(day.habit_id)
    assert store.habits(todo_list.id) == [] and store.habit_of(day.id) is None
    assert [t for t, _, _ in days_of(store, todo_list)] == ["Read more", "Read more"]
    assert store.spawn(TODAY + timedelta(days=1)) == []
    assert store.mark_habit_day(day.id, "failure").state == "failure"


def test_changing_a_habits_rule_restarts_it(store, todo_list):
    day = started_days_ago(store, todo_list, "h", 3)
    habit = store.update_habit(day.habit_id, interval=2)
    assert habit.start == TODAY and len(store.habit_days(todo_list.id)) == 4  # old days stay
    store.update_habit(habit.id, text="renamed")
    assert habit.start == TODAY


def test_habit_rejects_bad_changes(store, todo_list):
    habit = store.add_habit(todo_list.id, "h")
    with pytest.raises(ValueError):
        store.add_habit(todo_list.id, " ")
    with pytest.raises(ValueError):
        store.update_habit(habit.id, kind="weekdays")
    with pytest.raises(TypeError):
        store.update_habit(habit.id, start=TODAY)


def test_move_habit(store, todo_list):
    habits = [store.add_habit(todo_list.id, t) for t in "abc"]
    store.move_habit(habits[2].id, 0)
    assert [h.text for h in store.habits(todo_list.id)] == ["c", "a", "b"]


def test_deleting_a_list_hides_its_habits_and_days(store, todo_list):
    started_days_ago(store, todo_list, "h", 1)
    store.delete_list(todo_list.id)
    assert store.spawn(TODAY + timedelta(days=1)) == []
    assert store._live(HabitDay) == []


def test_habit_days_of_a_list_never_received_are_hidden(store):
    store._save(HabitDay(list_id=uuid4(), habit_id=uuid4(), text="h", day=TODAY, position="V"))
    assert store._live(HabitDay) == []



# Several processes on one database, each with its own store


@pytest.fixture
def other(path, store):
    """Another store on the same database, as another process would open it."""
    s = Store(path)
    yield s
    s.close()


def test_stores_see_each_others_writes(store, other, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    other.update_todo(todo.id, text="b")
    assert texts(store, todo_list.id) == ["b"]
    assert todo.text == "b"  # records loaded before reload too
    other.rename_list(todo_list.id, "Renamed")
    assert store.lists()[0].name == "Renamed"
    other.set_meta("k", "v")
    assert store.meta("k") == "v"
    other.set_meta("k", "changed")  # once cached here
    assert store.meta("k") == "changed"
    other.set_meta("k", None)
    assert store.meta("k") is None


def test_reading_never_blocks_another_store_writing(store, other, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    other.update_todo(todo.id, text="b")
    store.lists()
    assert todo.text == "b"  # a lazy load, outside any operation, leaves a read open
    other.update_todo(todo.id, text="c")
    store.update_todo(todo.id, done=True)  # writes after the other store's change
    assert [(t.text, t.done) for t in other.todos(todo_list.id)] == [("c", True)]


def test_a_failed_operation_leaves_the_database_free(store, other, todo_list):
    with pytest.raises(ValueError):
        store.add_list(" ")
    other.add_list("Next")
    assert [l.name for l in store.lists()] == ["Inbox", "Next"]


def test_stores_stamp_after_each_other_under_their_own_nodes(store, other, todo_list):
    assert store.clock.node != other.clock.node
    other.clock.millis += 10 * 60 * 1000  # ahead, as a faster clock would be
    ahead = other.add_list("Ahead")
    assert store.add_list("After").modified_at > ahead.modified_at
