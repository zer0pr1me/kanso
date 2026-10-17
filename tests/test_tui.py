import asyncio
from datetime import date, timedelta

import pytest
from textual.widgets import OptionList

from kanso.core.store import Store
from kanso.tui.app import KansoApp, todo_label


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "kanso.db")
    yield s
    s.close()


def label(text: str) -> str:
    """Label of an undone todo scheduled for today, the default."""
    return f"{date.today():%Y-%m-%d}  [ ] {text}"


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
    todo = store.add_todo(todo_list.id, "Buy milk", scheduled_at=None)
    assert str(todo_label(todo)) == "            [ ] Buy milk"
    todo = store.update_todo(todo.id, done=True, scheduled_at=date(2026, 10, 17))
    assert str(todo_label(todo)) == "2026-10-17  [x] Buy milk"


def test_labels_render_as_plain_text_not_markup(store):
    todo_list = store.add_list("[b]Lists[/b]")
    store.add_todo(todo_list.id, "[red]a[/red]", done=True, scheduled_at=None)

    async def check(app, pilot):
        rendered = lambda id: app.query_one(f"#{id}").render_line(0).text.strip()
        assert rendered("lists") == "[b]Lists[/b]"
        assert rendered("todos") == "[x] [red]a[/red]"

    run(KansoApp(store), check)


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
        assert prompts(app, "todos") == [label("a"), label("b")]

    run(KansoApp(store), check)


def test_highlighting_a_list_shows_its_todos(store):
    inbox, work = store.add_list("Inbox"), store.add_list("Work")
    store.add_todo(inbox.id, "a")
    store.add_todo(work.id, "c")

    async def check(app, pilot):
        app.query_one("#lists", OptionList).focus()
        await pilot.press("down")
        assert prompts(app, "todos") == [label("c")]
        await pilot.press("up")
        assert prompts(app, "todos") == [label("a")]

    run(KansoApp(store), check)


def texts(store, todo_list):
    return [t.text for t in store.todos(todo_list.id)]


@pytest.fixture
def inbox(store):
    todo_list = store.add_list("Inbox")
    for text in "ab":
        store.add_todo(todo_list.id, text)
    return todo_list


async def type_in_prompt(pilot, text: str, clear: bool = False):
    if clear:
        await pilot.press("ctrl+u")
    await pilot.press(*text, "enter")


def highlighted(app):
    widget = app.query_one("#todos", OptionList)
    return str(widget.get_option_at_index(widget.highlighted).prompt)


def test_add_appends_when_nothing_highlighted(store, inbox):
    async def check(app, pilot):
        await pilot.press("a")
        await type_in_prompt(pilot, "new")
        assert prompts(app, "todos") == [label("a"), label("b"), label("new")]
        assert highlighted(app) == label("new")
        assert app.focused.id == "todos"

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "b", "new"]


def test_add_inserts_below_highlighted(store, inbox):
    async def check(app, pilot):
        app.query_one("#todos", OptionList).highlighted = 0
        await pilot.press("a")
        await type_in_prompt(pilot, "new")
        assert highlighted(app) == label("new")

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "new", "b"]


def test_add_strips_text(store, inbox):
    async def check(app, pilot):
        await pilot.press("a", "space", "n", "space", "enter")

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "b", "n"]


@pytest.mark.parametrize("keys", [["escape"], ["space", "enter"], ["enter"]])
def test_add_cancelled_or_blank_does_nothing(store, inbox, keys):
    async def check(app, pilot):
        await pilot.press("a", *keys)
        assert prompts(app, "todos") == [label("a"), label("b")]
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "b"]


def test_add_without_lists_does_nothing(store):
    async def check(app, pilot):
        await pilot.press("a")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["e", "enter"])
def test_edit_highlighted_todo(store, inbox, key):
    async def check(app, pilot):
        todos = app.query_one("#todos", OptionList)
        todos.focus()
        todos.highlighted = 1
        await pilot.press(key)
        assert app.screen.query_one("Input").value == "b"
        await type_in_prompt(pilot, "c", clear=True)
        assert prompts(app, "todos") == [label("a"), label("c")]
        assert highlighted(app) == label("c")

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "c"]


@pytest.mark.parametrize("keys", [["escape"], ["ctrl+u", "enter"]])
def test_edit_cancelled_or_blank_keeps_text(store, inbox, keys):
    async def check(app, pilot):
        app.query_one("#todos", OptionList).highlighted = 0
        await pilot.press("e", *keys)
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "b"]


def test_edit_without_highlighted_todo_does_nothing(store, inbox):
    async def check(app, pilot):
        await pilot.press("e")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)


@pytest.mark.parametrize("key, days", [("full_stop", 1), ("comma", -1)])
def test_shift_date_of_highlighted_todo(store, inbox, key, days):
    async def check(app, pilot):
        app.query_one("#todos", OptionList).highlighted = 1
        await pilot.press(key, key)
        shifted = f"{date.today() + timedelta(days=2 * days):%Y-%m-%d}  [ ] b"
        assert prompts(app, "todos") == [label("a"), shifted]
        assert highlighted(app) == shifted

    run(KansoApp(store), check)


def test_shift_date_without_highlighted_todo_does_nothing(store, inbox):
    async def check(app, pilot):
        await pilot.press("full_stop", "comma")
        assert prompts(app, "todos") == [label("a"), label("b")]

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["enter", "right", "l"])
def test_entering_a_list_focuses_its_first_todo(store, inbox, key):
    async def check(app, pilot):
        await pilot.press(key)
        assert app.focused.id == "todos"
        assert highlighted(app) == label("a")
        await pilot.press("down", "e")
        assert app.screen.query_one("Input").value == "b"

    run(KansoApp(store), check)


def test_entering_keeps_highlighted_todo(store, inbox):
    async def check(app, pilot):
        app.query_one("#todos", OptionList).highlighted = 1
        await pilot.press("right")
        assert highlighted(app) == label("b")

    run(KansoApp(store), check)


def test_entering_empty_list_focuses_it_without_highlight(store):
    store.add_list("Empty")

    async def check(app, pilot):
        await pilot.press("enter")
        assert app.focused.id == "todos"
        assert app.query_one("#todos", OptionList).highlighted is None

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["left", "h", "escape"])
def test_leaving_todos_focuses_lists(store, inbox, key):
    async def check(app, pilot):
        await pilot.press("enter", key)
        assert app.focused.id == "lists"
        await pilot.press("down")
        assert app.query_one("#lists", OptionList).highlighted == 0  # only one list

    run(KansoApp(store), check)


def test_escape_in_prompt_cancels_without_leaving_todos(store, inbox):
    async def check(app, pilot):
        await pilot.press("enter", "e", "escape")
        assert app.screen is app.screen_stack[0]
        assert app.focused.id == "todos"

    run(KansoApp(store), check)


def test_vim_movement_in_lists(store):
    for name in ["Inbox", "Work", "Home"]:
        store.add_list(name)

    async def check(app, pilot):
        lists = app.query_one("#lists", OptionList)
        await pilot.press("j", "j")
        assert lists.highlighted == 2
        await pilot.press("k")
        assert lists.highlighted == 1
        await pilot.press("g")
        assert lists.highlighted == 0
        await pilot.press("G")
        assert lists.highlighted == 2

    run(KansoApp(store), check)


def test_vim_movement_in_todos(store, inbox):
    store.add_todo(inbox.id, "c")

    async def check(app, pilot):
        await pilot.press("l", "j")
        assert highlighted(app) == label("b")
        await pilot.press("G")
        assert highlighted(app) == label("c")
        await pilot.press("k")
        assert highlighted(app) == label("b")
        await pilot.press("g")
        assert highlighted(app) == label("a")

    run(KansoApp(store), check)


def test_vim_and_date_keys_type_into_prompt(store, inbox):
    async def check(app, pilot):
        await pilot.press("a", *"hjklgG", "full_stop", "comma", "enter")

    run(KansoApp(store), check)
    assert [t.text for t in store.todos(inbox.id)] == ["a", "b", "hjklgG.,"]
