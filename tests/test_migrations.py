import sqlite3
from contextlib import closing

import pytest

from kanso.core import migrations
from kanso.core.migrations import current_version, upgrade
from kanso.core.store import Store


@pytest.fixture
def path(tmp_path):
    return tmp_path / "kanso.db"


def sql(path, query: str, *params):
    with closing(sqlite3.connect(path)) as conn, conn:
        return conn.execute(query, params).fetchall()


def stored_version(path) -> str | None:
    rows = sql(path, "SELECT value FROM meta WHERE key = 'schema_version'")
    return rows[0][0] if rows else None


def open_store(path) -> Store:
    store = Store(path)
    store.close()
    return store


@pytest.fixture
def old_db(path):
    """A database at schema 0 where todo's `text` column was still called `title`."""
    store = Store(path)
    inbox = store.add_list("Inbox")
    store.add_todo(inbox.id, "a")
    store.close()
    sql(path, "ALTER TABLE todo RENAME COLUMN text TO title")
    sql(path, "UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    return store.clock.node


def rename_title(rows):
    return {**rows, "todo": [{("text" if k == "title" else k): v for k, v in row.items()} for row in rows["todo"]]}


def test_fresh_database_gets_current_version(path):
    open_store(path)
    assert stored_version(path) == str(current_version())


def test_reopening_keeps_data_and_version(path):
    store = Store(path)
    store.add_list("Inbox")
    store.close()
    store = Store(path)
    assert [l.name for l in store.lists()] == ["Inbox"]
    store.close()
    assert stored_version(path) == str(current_version())


def test_upgrades_old_database(path, old_db, monkeypatch):
    monkeypatch.setattr(migrations, "MIGRATIONS", [rename_title])
    store = Store(path)
    [inbox] = store.lists()
    assert [t.text for t in store.todos(inbox.id)] == ["a"]
    assert store.clock.node == old_db
    store.add_todo(inbox.id, "b")
    assert [t.text for t in store.todos(inbox.id)] == ["a", "b"]
    store.close()
    assert stored_version(path) == "1"


def test_unversioned_database_counts_as_version_zero(path, old_db, monkeypatch):
    sql(path, "DELETE FROM meta WHERE key = 'schema_version'")
    monkeypatch.setattr(migrations, "MIGRATIONS", [rename_title])
    open_store(path)
    assert sql(path, "SELECT text FROM todo") == [("a",)]


def test_runs_only_pending_migrations_in_order(path, old_db, monkeypatch):
    ran = []

    def mark(name):
        return lambda rows: ran.append(name) or rows

    sql(path, "UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    monkeypatch.setattr(migrations, "MIGRATIONS", [mark("first"), rename_title, mark("third")])
    open_store(path)
    assert ran == ["third"] and stored_version(path) == "3"


def test_newer_database_is_refused_untouched(path, monkeypatch):
    open_store(path)
    sql(path, "UPDATE meta SET value = '5' WHERE key = 'schema_version'")
    with pytest.raises(ValueError, match="newer"):
        Store(path)
    assert stored_version(path) == "5"


def failing(rows):
    raise RuntimeError("boom")


def extra_table(rows):
    return {**rename_title(rows), "gone": [{"x": 1}]}


@pytest.mark.parametrize("migration, error", [(failing, RuntimeError), (extra_table, ValueError)])
def test_failed_migration_rolls_back(path, old_db, monkeypatch, migration, error):
    monkeypatch.setattr(migrations, "MIGRATIONS", [migration])
    with pytest.raises(error):
        Store(path)
    assert stored_version(path) == "0"
    assert sql(path, "SELECT title FROM todo") == [("a",)]


def test_upgrade_rows_from_older_version(monkeypatch):
    monkeypatch.setattr(migrations, "MIGRATIONS", [rename_title, lambda rows: {**rows, "seen": True}])
    rows = {"todo": [{"id": "1", "title": "a"}]}
    assert upgrade(rows, 0) == {"todo": [{"id": "1", "text": "a"}], "seen": True}
    assert upgrade(rows, 1) == {**rows, "seen": True}
    assert upgrade(rows, 2) == rows


def test_upgrade_rows_from_newer_version_refused():
    with pytest.raises(ValueError, match="newer"):
        upgrade({}, current_version() + 1)
