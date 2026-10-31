from datetime import date, timedelta
from uuid import uuid4, uuid5

import pytest
from sqlmodel import select

from conftest import texts
from kanso.core import store as store_module
from kanso.core.models import SYNCED_MODELS, Repeat, Todo, TodoList, device_id
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
    s.add_device("k", "Laptop")
    before = {m: [r.model_dump() for r in s.db.exec(select(m))] for m in SYNCED_MODELS}
    node = s.clock.node
    s.close()
    s = Store(path)
    assert {m: [r.model_dump() for r in s.db.exec(select(m))] for m in SYNCED_MODELS} == before
    assert s.clock.node == node
    s.close()


@pytest.mark.parametrize("model", SYNCED_MODELS)
def test_clock_resumes_after_stored_stamps(path, model):
    s = Store(path)
    todo_list = s.add_list("Inbox")
    s.add_repeat(todo_list.id, "r")
    s.add_device("k", "Laptop")
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
    repeat = store.add_repeat(todo_list.id, "r")
    [todo] = store.todos(todo_list.id)
    for record in (todo_list, repeat, todo):
        fields = record.model_dump().keys() - {"id", "modified_at", "stamps"}
        assert record.stamps == dict.fromkeys(fields, record.modified_at)


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
    store.delete_todo(todo.id)
    store.delete_repeat(repeat.id)
    store.delete_list(todo_list.id)
    return {TodoList: todo_list.id, Todo: todo.id, Repeat: repeat.id}


OPERATIONS = {
    TodoList: [
        lambda s, id: s.rename_list(id, "x"),
        lambda s, id: s.delete_list(id),
        lambda s, id: s.todos(id),
        lambda s, id: s.repeats(id),
        lambda s, id: s.add_todo(id, "x"),
        lambda s, id: s.add_repeat(id, "x"),
    ],
    Todo: [
        lambda s, id: s.update_todo(id, text="x"),
        lambda s, id: s.shift_todo(id, 1),
        lambda s, id: s.move_todo(id, 0),
        lambda s, id: s.delete_todo(id),
        lambda s, id: s.repeat_of(id),
    ],
    Repeat: [
        lambda s, id: s.update_repeat(id, text="x"),
        lambda s, id: s.delete_repeat(id),
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
    assert store.spawn_repeats(date.today() + timedelta(days=1)) == []


def test_records_of_a_list_never_received_are_hidden(store, todo_list):
    """As when sync delivers a todo or repeat before its list."""
    store._save(
        Todo(list_id=uuid4(), text="a", position="V", labels=["orphan"]),
        Repeat(list_id=uuid4(), text="r", position="V"),
    )
    assert store.labels() == []
    assert store.spawn_repeats() == []


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


def test_delete_todo_tombstones_it(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    store.add_todo(todo_list.id, "b")
    store.delete_todo(todo.id)
    assert texts(store, todo_list.id) == ["b"]
    assert store.db.get(Todo, todo.id).deleted


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
    store.spawn_repeats(date.today() + timedelta(days=1))
    store.update_todo(store.todos(todo_list.id)[0].id, done=True)
    before = [(t.text, t.modified_at, dict(t.stamps)) for t in store.todos(todo_list.id)]
    store.update_repeat(repeat.id, text="b")
    assert texts(store, todo_list.id) == ["b", "o", "b", "o"]
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
    assert store.spawn_repeats(date.today() + timedelta(days=1)) == []


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
    spawned = store.spawn_repeats(TOMORROW)
    assert sorted((t.text, t.scheduled_at) for t in spawned) == [("daily", TOMORROW), ("tomorrow", TOMORROW)]
    assert texts(store, todo_list.id) == ["today", "tomorrow"]


def test_spawn_puts_several_repeats_in_order(store, todo_list):
    for text in "abc":
        store.add_repeat(todo_list.id, text)
    store.spawn_repeats(TOMORROW)
    assert texts(store, todo_list.id) == ["a", "b", "c"] * 2


def test_spawn_is_idempotent_and_skips_changed_or_deleted_occurrences(store, todo_list):
    store.add_repeat(todo_list.id, "a")
    assert store.spawn_repeats() == []
    [todo] = store.todos(todo_list.id)
    store.update_todo(todo.id, done=True)
    store.shift_todo(todo.id, 1)
    assert store.spawn_repeats() == []
    store.delete_todo(todo.id)
    assert store.spawn_repeats() == []
    assert texts(store, todo_list.id) == []


# Devices and local settings


def test_add_device_trusts_its_key(store):
    laptop = store.add_device("key-1", " Laptop ")
    assert laptop.id == device_id("key-1") and laptop.name == "Laptop"
    assert store.is_trusted("key-1") and not store.is_trusted("key-2")
    store.add_device("key-2", "Phone")
    assert [d.name for d in store.devices()] == ["Laptop", "Phone"]


def test_re_adding_a_device_renames_and_restores_it(store):
    device = store.add_device("key-1", "Laptop")
    device.deleted = True
    store._save(device)
    assert not store.is_trusted("key-1")
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
