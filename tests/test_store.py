from datetime import date, timedelta
from uuid import uuid4, uuid5

import pytest
from sqlmodel import select

from kanso.core import store as store_module
from kanso.core.models import Repeat, Todo, TodoList
from kanso.core.store import NotFound, Store


@pytest.fixture
def path(tmp_path):
    return tmp_path / "kanso.db"


@pytest.fixture
def store(path):
    s = Store(path)
    yield s
    s.close()


@pytest.fixture
def todo_list(store):
    return store.add_list("Inbox")


def texts(store, list_id):
    return [t.text for t in store.todos(list_id)]


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


def test_default_path_from_env_creates_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KANSO_DATA_DIR", str(tmp_path / "a" / "b"))
    assert store_module.default_path() == tmp_path / "a" / "b" / "kanso.db"
    assert (tmp_path / "a" / "b").is_dir()


def test_default_path_from_env_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KANSO_DATA_DIR", "~/kanso")
    assert store_module.default_path() == tmp_path / "kanso" / "kanso.db"


def test_store_opens_database_in_env_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KANSO_DATA_DIR", str(tmp_path / "data"))
    store = Store()
    store.add_list("Inbox")
    store.close()
    assert (tmp_path / "data" / "kanso.db").is_file()


def test_data_persists_and_node_id_is_stable(path):
    s = Store(path)
    s.add_list("Inbox")
    node = s.clock.node
    s.close()
    s = Store(path)
    assert [l.name for l in s.lists()] == ["Inbox"]
    assert s.clock.node == node
    s.close()


def test_clock_resumes_after_stored_stamps(path):
    s = Store(path)
    stamp = s.add_list("Inbox").modified_at
    s.close()
    s = Store(path)
    assert s.clock.now() > stamp
    s.close()


def test_every_write_stamps_monotonically(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    first = todo.modified_at
    assert first > todo_list.modified_at
    assert store.update_todo(todo.id, text="b").modified_at > first


# Lists


def test_lists_keep_creation_order(store):
    for name in ["b", "a", "c"]:
        store.add_list(name)
    assert [l.name for l in store.lists()] == ["b", "a", "c"]


def test_add_list_strips_name(store):
    assert store.add_list("  Work  ").name == "Work"


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_list_name_validation(store, todo_list, name):
    with pytest.raises(ValueError):
        store.add_list(name)
    with pytest.raises(ValueError):
        store.rename_list(todo_list.id, name)
    assert store.lists()[0].name == "Inbox"


def test_rename_list(store, todo_list):
    store.rename_list(todo_list.id, "Today")
    assert [l.name for l in store.lists()] == ["Today"]


def test_delete_list_tombstones_it_and_its_todos(store, todo_list):
    other = store.add_list("Other")
    store.add_todo(todo_list.id, "a")
    kept = store.add_todo(other.id, "b")
    store.delete_list(todo_list.id)
    assert [l.name for l in store.lists()] == ["Other"]
    assert store.todos(other.id) == [kept]
    gone = store.db.exec(select(Todo).where(Todo.list_id == todo_list.id)).all()
    assert len(gone) == 1 and gone[0].deleted
    assert store.db.get(TodoList, todo_list.id).deleted
    with pytest.raises(NotFound):
        store.todos(todo_list.id)


@pytest.mark.parametrize("op", ["rename", "delete", "todos", "add_todo"])
def test_missing_or_deleted_list_not_found(store, todo_list, op):
    store.delete_list(todo_list.id)
    calls = {
        "rename": lambda id: store.rename_list(id, "x"),
        "delete": store.delete_list,
        "todos": store.todos,
        "add_todo": store.add_todo,
    }
    for id in (todo_list.id, uuid4()):
        with pytest.raises(NotFound):
            calls[op](id)


# Todos


def test_add_todo_defaults(store, todo_list):
    todo = store.add_todo(todo_list.id)
    today = date.today()
    assert (todo.text, todo.done, todo.created_at, todo.scheduled_at, todo.completed_at) == ("", False, today, today, None)


def test_add_todo_with_fields(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", done=True, scheduled_at=None)
    assert (todo.done, todo.completed_at, todo.scheduled_at) == (True, date.today(), None)


def test_add_todo_with_fields_stamps_once(store, todo_list, monkeypatch):
    stamps = []
    now = store.clock.now
    monkeypatch.setattr(store.clock, "now", lambda: stamps.append(now()) or stamps[-1])
    todo = store.add_todo(todo_list.id, "a", done=True)
    assert stamps == [todo.modified_at]


def test_add_todo_rejects_other_fields(store, todo_list):
    with pytest.raises(TypeError):
        store.add_todo(todo_list.id, "a", deleted=True)
    assert store.todos(todo_list.id) == []


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
    store.update_todo(todo.id, text="b", scheduled_at=None)
    assert (store.todos(todo_list.id)[0].text, store.todos(todo_list.id)[0].scheduled_at) == ("b", None)


def test_toggling_done_sets_and_clears_completed_at(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    assert store.update_todo(todo.id, done=True).completed_at == date.today()
    assert store.update_todo(todo.id, done=False).completed_at is None


def test_setting_done_again_keeps_completed_at(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    store.update_todo(todo.id, done=True)
    todo.completed_at = date(2026, 1, 1)
    assert store.update_todo(todo.id, done=True, text="b").completed_at == date(2026, 1, 1)


@pytest.mark.parametrize("field", ["id", "list_id", "position", "deleted", "completed_at", "bogus"])
def test_update_rejects_other_fields(store, todo_list, field):
    todo = store.add_todo(todo_list.id, "a")
    with pytest.raises(TypeError):
        store.update_todo(todo.id, **{field: None})


@pytest.mark.parametrize("index, expected", [(0, "cab"), (1, "acb"), (2, "abc"), (99, "abc"), (-1, "cab")])
def test_move_todo(store, todo_list, index, expected):
    todos = [store.add_todo(todo_list.id, t) for t in "abc"]
    store.move_todo(todos[2].id, index)
    assert "".join(texts(store, todo_list.id)) == expected


def test_move_todo_down(store, todo_list):
    todos = [store.add_todo(todo_list.id, t) for t in "abc"]
    store.move_todo(todos[0].id, 1)
    assert "".join(texts(store, todo_list.id)) == "bac"


def test_delete_todo_tombstones_it(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    store.add_todo(todo_list.id, "b")
    store.delete_todo(todo.id)
    assert texts(store, todo_list.id) == ["b"]
    assert store.db.get(Todo, todo.id).deleted


@pytest.mark.parametrize("op", ["update", "move", "delete"])
def test_missing_or_deleted_todo_not_found(store, todo_list, op):
    todo = store.add_todo(todo_list.id, "a")
    store.delete_todo(todo.id)
    calls = {
        "update": lambda id: store.update_todo(id, text="x"),
        "move": lambda id: store.move_todo(id, 0),
        "delete": store.delete_todo,
    }
    for id in (todo.id, uuid4()):
        with pytest.raises(NotFound):
            calls[op](id)


def test_todos_of_deleted_list_not_found(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    store.delete_list(todo_list.id)
    with pytest.raises(NotFound):
        store.update_todo(todo.id, text="x")


@pytest.mark.parametrize("days", [1, -1, 7, 0])
def test_shift_todo(store, todo_list, days):
    todo = store.add_todo(todo_list.id, "a", scheduled_at=date(2026, 1, 31))
    assert store.shift_todo(todo.id, days).scheduled_at == date(2026, 1, 31) + timedelta(days=days)


def test_shift_unscheduled_todo_counts_from_today(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", scheduled_at=None)
    assert store.shift_todo(todo.id, 1).scheduled_at == date.today() + timedelta(days=1)


def test_shift_missing_or_deleted_todo_not_found(store, todo_list):
    todo = store.add_todo(todo_list.id, "a")
    store.delete_todo(todo.id)
    for id in (todo.id, uuid4()):
        with pytest.raises(NotFound):
            store.shift_todo(id, 1)


# Labels


def test_new_todo_has_no_labels(store, todo_list):
    assert store.add_todo(todo_list.id, "a").labels == []


def test_add_and_update_labels(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", labels=["home"])
    store.update_todo(todo.id, labels=["work", "urgent"])
    assert store.todos(todo_list.id)[0].labels == ["work", "urgent"]


def test_labels_persist(path):
    s = Store(path)
    todo = s.add_todo(s.add_list("Inbox").id, "a", labels=["home"])
    s.close()
    s = Store(path)
    assert s.todos(todo.list_id)[0].labels == ["home"]
    s.close()


def test_labels_are_cleaned(store, todo_list):
    todo = store.add_todo(todo_list.id, "a", labels=[" home ", "#work", "", "#", "home", "Home"])
    assert todo.labels == ["home", "work", "Home"]


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


def test_label_at_max_length(store, todo_list):
    assert store.add_todo(todo_list.id, "a", labels=["x" * 50]).labels == ["x" * 50]


def test_labels_lists_live_labels_sorted(store, todo_list):
    other = store.add_list("Other")
    store.add_todo(todo_list.id, "a", labels=["work", "Home"])
    store.add_todo(other.id, "b", labels=["errands", "work"])
    gone = store.add_todo(todo_list.id, "c", labels=["deleted"])
    store.delete_todo(gone.id)
    store.add_todo(store.add_list("Gone").id, "d", labels=["gone"])
    store.delete_list(store.lists()[-1].id)
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


def test_add_repeat_with_rule(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a", "weekdays", weekdays=[4, 0])
    assert (repeat.kind, repeat.weekdays) == ("weekdays", [0, 4])


def test_repeats_keep_order_and_are_per_list(store, todo_list):
    other = store.add_list("Other")
    for text in "bac":
        store.add_repeat(todo_list.id, text)
    store.add_repeat(other.id, "x")
    assert [r.text for r in store.repeats(todo_list.id)] == ["b", "a", "c"]
    assert [r.text for r in store.repeats(other.id)] == ["x"]


def test_repeats_persist(path):
    s = Store(path)
    repeat = s.add_repeat(s.add_list("Inbox").id, "a", "monthdays", monthdays=[1, 15])
    s.close()
    s = Store(path)
    [loaded] = s.repeats(repeat.list_id)
    assert (loaded.id, loaded.monthdays, loaded.start) == (repeat.id, [1, 15], date.today())
    s.close()


@pytest.mark.parametrize("text", ["", "   "])
def test_repeat_text_must_not_be_blank(store, todo_list, text):
    repeat = store.add_repeat(todo_list.id, "a")
    with pytest.raises(ValueError):
        store.add_repeat(todo_list.id, text)
    with pytest.raises(ValueError):
        store.update_repeat(repeat.id, text=text)
    assert [r.text for r in store.repeats(todo_list.id)] == ["a"]


def test_invalid_rule_is_rejected_unchanged(store, todo_list):
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


def test_update_repeat_text_keeps_rule_and_start(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a", "days", interval=3)
    repeat.start = date(2026, 1, 1)
    store.update_repeat(repeat.id, text=" b ")
    [updated] = store.repeats(todo_list.id)
    assert (updated.text, updated.interval, updated.start) == ("b", 3, date(2026, 1, 1))


def test_changing_rule_restarts_it_today(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a", "days", interval=3)
    repeat.start = date(2026, 1, 1)
    store.update_repeat(repeat.id, interval=3)
    assert repeat.start == date(2026, 1, 1)
    store.update_repeat(repeat.id, kind="weekdays", weekdays=[2])
    assert (repeat.kind, repeat.interval, repeat.weekdays, repeat.start) == ("weekdays", 1, [2], date.today())


def test_delete_repeat_tombstones_it_keeping_its_todos(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a")
    store.spawn_repeats()
    store.delete_repeat(repeat.id)
    assert store.repeats(todo_list.id) == []
    assert store.db.get(Repeat, repeat.id).deleted
    assert texts(store, todo_list.id) == ["a"]


def test_delete_list_tombstones_its_repeats(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a")
    store.delete_list(todo_list.id)
    assert store.db.get(Repeat, repeat.id).deleted
    assert store.spawn_repeats() == []


@pytest.mark.parametrize("op", ["update", "delete"])
def test_missing_or_deleted_repeat_not_found(store, todo_list, op):
    repeat = store.add_repeat(todo_list.id, "a")
    store.delete_repeat(repeat.id)
    calls = {"update": lambda id: store.update_repeat(id, text="x"), "delete": store.delete_repeat}
    for id in (repeat.id, uuid4()):
        with pytest.raises(NotFound):
            calls[op](id)


@pytest.mark.parametrize("op", ["repeats", "add_repeat"])
def test_repeats_of_missing_list_not_found(store, op):
    calls = {"repeats": store.repeats, "add_repeat": lambda id: store.add_repeat(id, "a")}
    with pytest.raises(NotFound):
        calls[op](uuid4())


# Spawning repeats


def test_spawn_appends_todo_for_today(store, todo_list):
    store.add_todo(todo_list.id, "first", scheduled_at=None)
    repeat = store.add_repeat(todo_list.id, "a")
    [todo] = store.spawn_repeats()
    assert texts(store, todo_list.id) == ["first", "a"]
    assert (todo.repeat_id, todo.scheduled_at, todo.done, todo.labels) == (repeat.id, date.today(), False, [])
    assert todo.id == uuid5(repeat.id, date.today().isoformat())


def test_spawn_only_matching_repeats_on_given_day(store, todo_list):
    other = store.add_list("Other")
    day = date.today() + timedelta(days=1)
    store.add_repeat(todo_list.id, "tomorrow", "weekdays", weekdays=[day.weekday()])
    store.add_repeat(todo_list.id, "today", "weekdays", weekdays=[date.today().weekday()])
    store.add_repeat(other.id, "daily")
    assert sorted((t.text, t.scheduled_at) for t in store.spawn_repeats(day)) == [("daily", day), ("tomorrow", day)]
    assert texts(store, todo_list.id) == ["tomorrow"]
    assert texts(store, other.id) == ["daily"]


def test_spawn_puts_several_repeats_in_order(store, todo_list):
    for text in "abc":
        store.add_repeat(todo_list.id, text)
    store.spawn_repeats()
    assert texts(store, todo_list.id) == ["a", "b", "c"]


def test_spawn_is_idempotent(store, todo_list):
    store.add_repeat(todo_list.id, "a")
    store.spawn_repeats()
    assert store.spawn_repeats() == []
    assert texts(store, todo_list.id) == ["a"]


def test_spawn_skips_changed_or_deleted_occurrences(store, todo_list):
    store.add_repeat(todo_list.id, "a")
    [todo] = store.spawn_repeats()
    store.shift_todo(todo.id, 1)
    store.update_todo(todo.id, text="b", done=True)
    assert store.spawn_repeats() == []
    store.delete_todo(todo.id)
    assert store.spawn_repeats() == []
    assert texts(store, todo_list.id) == []


def test_spawn_again_on_the_next_day(store, todo_list):
    store.add_repeat(todo_list.id, "a")
    store.spawn_repeats()
    tomorrow = date.today() + timedelta(days=1)
    [todo] = store.spawn_repeats(tomorrow)
    assert todo.scheduled_at == tomorrow
    assert texts(store, todo_list.id) == ["a", "a"]


def test_spawn_ignores_deleted_repeats(store, todo_list):
    repeat = store.add_repeat(todo_list.id, "a")
    store.delete_repeat(repeat.id)
    assert store.spawn_repeats() == []


def test_spawned_todo_persists_its_repeat(path):
    s = Store(path)
    repeat = s.add_repeat(s.add_list("Inbox").id, "a")
    s.spawn_repeats()
    s.close()
    s = Store(path)
    assert [t.repeat_id for t in s.todos(repeat.list_id)] == [repeat.id]
    assert s.spawn_repeats() == []
    s.close()


def test_clock_resumes_after_repeat_stamps(path):
    s = Store(path)
    repeat = s.add_repeat(s.add_list("Inbox").id, "a")
    s.close()
    s = Store(path)
    assert s.clock.now() > repeat.modified_at
    s.close()
