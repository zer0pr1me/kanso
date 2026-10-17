from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlmodel import select

from kanso.core import store as store_module
from kanso.core.models import Todo, TodoList
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


def test_default_path_is_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "user_data_path", lambda app, ensure_exists: tmp_path / app)
    assert store_module.default_path() == tmp_path / "kanso" / "kanso.db"


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
