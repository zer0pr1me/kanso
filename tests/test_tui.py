import asyncio
from datetime import date

import pytest
from textual.widgets import OptionList

from kanso.core.store import Store
from kanso.tui.app import KansoApp, todo_label


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "kanso.db")
    yield s
    s.close()


def run(app: KansoApp, check):
    async def main():
        async with app.run_test() as pilot:
            await pilot.pause()
            await check(app, pilot)

    asyncio.run(main())


def prompts(app: KansoApp, id: str) -> list[str]:
    widget = app.query_one(f"#{id}", OptionList)
    return [str(widget.get_option_at_index(i).prompt) for i in range(widget.option_count)]


def test_todo_label(store):
    todo_list = store.add_list("Inbox")
    todo = store.add_todo(todo_list.id, "Buy milk")
    assert todo_label(todo) == "[ ] Buy milk"
    todo = store.update_todo(todo.id, done=True, scheduled_at=date(2026, 10, 17))
    assert todo_label(todo) == "[x] Buy milk  (2026-10-17)"


def test_empty_store(store):
    async def check(app, pilot):
        assert prompts(app, "lists") == []
        assert prompts(app, "todos") == []

    run(KansoApp(store), check)


def test_shows_lists_and_first_lists_todos(store):
    inbox, work = store.add_list("Inbox"), store.add_list("Work")
    store.add_todo(inbox.id, "a")
    store.add_todo(inbox.id, "b")
    store.add_todo(work.id, "c")

    async def check(app, pilot):
        assert prompts(app, "lists") == ["Inbox", "Work"]
        assert prompts(app, "todos") == ["[ ] a", "[ ] b"]

    run(KansoApp(store), check)


def test_highlighting_a_list_shows_its_todos(store):
    inbox, work = store.add_list("Inbox"), store.add_list("Work")
    store.add_todo(inbox.id, "a")
    store.add_todo(work.id, "c")

    async def check(app, pilot):
        app.query_one("#lists", OptionList).focus()
        await pilot.press("down")
        assert prompts(app, "todos") == ["[ ] c"]
        await pilot.press("up")
        assert prompts(app, "todos") == ["[ ] a"]

    run(KansoApp(store), check)
