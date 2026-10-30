from datetime import date, timedelta

import pytest
from textual.widgets import ContentSwitcher, Input, OptionList

from conftest import notifications, run, texts
from kanso.core.store import NAME_MAX
from kanso.tui import app as app_module
from kanso.tui.app import KansoApp
from kanso.tui.dialogs import RepeatDialog, TextPrompt

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


def label(text: str, done: bool = False) -> str:
    return f"{'[x]' if done else '[ ]'} {text}"


def prompts(app, pane: str) -> list[str]:
    widget = app.query_one(f"#{pane}", OptionList)
    return [str(widget.get_option_at_index(i).prompt) for i in range(widget.option_count)]


def highlighted(app, pane: str = "todos") -> str | None:
    widget = app.query_one(f"#{pane}", OptionList)
    return None if widget.highlighted is None else str(widget.get_option_at_index(widget.highlighted).prompt)


def on_main_screen(app) -> bool:
    return app.screen is app.screen_stack[0]


async def type_in_prompt(pilot, text: str, clear: bool = False):
    await pilot.press(*(["ctrl+u"] if clear else []), *text, "enter")


@pytest.fixture
def inbox(store):
    """Inbox with todos "a" and "b" for today."""
    todo_list = store.add_list("Inbox")
    for text in "ab":
        store.add_todo(todo_list.id, text)
    return todo_list


@pytest.fixture
def dated(store):
    """Inbox with "a" and "c" for tomorrow and "b" for today, added in that order."""
    todo_list = store.add_list("Inbox")
    for text, day in [("a", TOMORROW), ("b", TODAY), ("c", TOMORROW)]:
        store.add_todo(todo_list.id, text, scheduled_at=day)
    return todo_list


@pytest.fixture
def repeating(store):
    """Inbox with daily repeats "x" and "y", so their todos for today."""
    todo_list = store.add_list("Inbox")
    for text in "xy":
        store.add_repeat(todo_list.id, text)
    return todo_list


# Layout and focus


def test_empty_store(store):
    async def check(app, pilot):
        assert prompts(app, "lists") == prompts(app, "todos") == []

    run(KansoApp(store), check)


def test_highlighting_a_list_shows_its_todos_and_repeats(store):
    inbox, work = store.add_list("Inbox"), store.add_list("Work")
    store.add_todo(inbox.id, "a")
    store.add_repeat(work.id, "r", interval=2)

    async def check(app, pilot):
        assert prompts(app, "lists") == ["Inbox", "Work"]
        assert prompts(app, "todos") == ["Today", label("a")]
        await pilot.press("j")
        assert prompts(app, "todos") == ["Today", label("r ↻")]
        assert prompts(app, "repeats") == ["r  Every 2 days "]

    run(KansoApp(store), check)


def test_text_renders_plainly_not_as_markup(store):
    todo_list = store.add_list("[b]Lists[/b]")
    store.add_todo(todo_list.id, "[red]a[/red]", scheduled_at=None)

    async def check(app, pilot):
        def line(pane, y=0):
            return app.query_one(f"#{pane}").render_line(y).text.strip()

        assert (line("lists"), line("todos"), line("todos", 1)) == ("[b]Lists[/b]", "No date", "[ ] [red]a[/red]")

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["enter", "right", "l"])
def test_entering_a_list_focuses_its_first_todo(store, dated, key):
    async def check(app, pilot):
        await pilot.press(key)
        assert app.focused.id == "todos" and highlighted(app) == label("b")

    run(KansoApp(store), check)


def test_entering_keeps_highlighted_todo(store, inbox):
    async def check(app, pilot):
        app.query_one("#todos", OptionList).highlighted = 2
        await pilot.press("l")
        assert highlighted(app) == label("b")

    run(KansoApp(store), check)


def test_entering_empty_list_highlights_nothing(store):
    store.add_list("Empty")

    async def check(app, pilot):
        await pilot.press("l")
        assert app.focused.id == "todos" and highlighted(app) is None

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["left", "h", "escape"])
def test_leaving_a_pane_focuses_lists(store, inbox, key):
    async def check(app, pilot):
        for _ in ["todos", "repeats"]:
            await pilot.press("l", key)
            assert app.focused.id == "lists"
            await pilot.press("r")

    run(KansoApp(store), check)


def test_r_toggles_panes_keeping_focus_side(store, repeating):
    async def check(app, pilot):
        switcher = app.query_one("#panes", ContentSwitcher)
        await pilot.press("r")
        assert switcher.current == "repeats" and app.focused.id == "lists"
        assert prompts(app, "repeats") == ["x  Daily ", "y  Daily "]
        await pilot.press("l")
        assert app.focused.id == "repeats" and highlighted(app, "repeats") == "x  Daily "
        await pilot.press("r")
        assert switcher.current == "todos" and app.focused.id == "todos"

    run(KansoApp(store), check)


def test_vim_movement(store):
    for name in ["Inbox", "Work", "Home"]:
        store.add_list(name)

    async def check(app, pilot):
        lists = app.query_one("#lists", OptionList)
        for key, expected in [("j", 1), ("j", 2), ("k", 1), ("g", 0), ("G", 2)]:
            await pilot.press(key)
            assert lists.highlighted == expected

    run(KansoApp(store), check)


def test_cursor_skips_date_titles(store, dated):
    async def check(app, pilot):
        assert prompts(app, "todos") == ["Today", label("b"), "Tomorrow", label("a"), label("c")]
        await pilot.press("l", "j")
        assert highlighted(app) == label("a")
        await pilot.press("k", "k", "g")
        assert highlighted(app) == label("b")

    run(KansoApp(store), check)


# Keys act only where they apply


@pytest.mark.parametrize("key", ["space", "number_sign", "d", "full_stop", "comma"])
def test_todo_keys_do_nothing_elsewhere(store, inbox, key):
    async def check(app, pilot):
        await pilot.press(key)  # lists focused
        await pilot.press("r", "l", key)  # repeats focused
        await pilot.press("r")
        app.query_one("#todos", OptionList).highlighted = None
        await pilot.press(key)  # todos focused, nothing highlighted
        assert on_main_screen(app)
        assert prompts(app, "todos") == ["Today", label("a"), label("b")]

    run(KansoApp(store), check)


@pytest.mark.parametrize("pane, key", [("todos", "e"), ("repeats", "e"), ("repeats", "d"), ("lists", "e")])
def test_edit_and_delete_need_a_highlight(store, pane, key):
    async def check(app, pilot):
        if pane == "repeats":
            await pilot.press("r")
        app.query_one(f"#{pane}").focus()
        await pilot.press(key)
        assert on_main_screen(app)

    run(KansoApp(store), check)


@pytest.mark.parametrize("pane", ["todos", "repeats"])
def test_add_needs_a_list(store, pane):
    async def check(app, pilot):
        if pane == "repeats":
            await pilot.press("r")
        app.query_one(f"#{pane}").focus()
        await pilot.press("a")
        assert on_main_screen(app)

    run(KansoApp(store), check)


def test_keys_type_into_prompts(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "a", *"aedhjklgGr#", "space", "full_stop", "comma", "enter")
        assert app.focused.id == "todos"

    run(KansoApp(store), check)
    assert texts(store, inbox.id) == ["a", "aedhjklgGr# .,", "b"]


# Lists


def test_add_list_appends_and_highlights_it(store, inbox):
    async def check(app, pilot):
        await pilot.press("a")
        field = app.screen.query_one(Input)
        assert (field.placeholder, field.max_length) == ("List", NAME_MAX)
        await type_in_prompt(pilot, "Home")
        assert prompts(app, "lists") == ["Inbox", "Home"]
        assert highlighted(app, "lists") == "Home" and app.focused.id == "lists"
        assert prompts(app, "todos") == []

    run(KansoApp(store), check)


def test_rename_list(store, inbox):
    async def check(app, pilot):
        await pilot.press("e")
        assert app.screen.query_one(Input).value == "Inbox"
        await type_in_prompt(pilot, "Job", clear=True)
        assert prompts(app, "lists") == ["Job"] and prompts(app, "todos")[1:] == [label("a"), label("b")]

    run(KansoApp(store), check)


def test_first_list_then_todo(store):
    async def check(app, pilot):
        await pilot.press("a")
        await type_in_prompt(pilot, "Inbox")
        await pilot.press("l", "a")
        await type_in_prompt(pilot, "milk")
        assert prompts(app, "todos") == ["Today", label("milk")]

    run(KansoApp(store), check)


# Todos


def test_add_without_highlight_appends_for_today(store, dated):
    async def check(app, pilot):
        app.query_one("#todos").focus()
        await pilot.press("a")
        assert app.screen.query_one(Input).max_length == 0
        await type_in_prompt(pilot, "new")
        assert prompts(app, "todos") == ["Today", label("b"), label("new"), "Tomorrow", label("a"), label("c")]
        assert highlighted(app) == label("new")

    run(KansoApp(store), check)


def test_add_inserts_below_highlighted_on_its_date(store, dated):
    async def check(app, pilot):
        await pilot.press("l", "j", "a")
        await type_in_prompt(pilot, "new")
        assert prompts(app, "todos")[3:] == [label("a"), label("new"), label("c")]

    run(KansoApp(store), check)
    assert texts(store, dated.id) == ["a", "new", "b", "c"]


def test_add_below_unscheduled_stays_unscheduled(store):
    store.add_todo(store.add_list("Inbox").id, "a", scheduled_at=None)

    async def check(app, pilot):
        await pilot.press("l", "a")
        await type_in_prompt(pilot, "b")
        assert prompts(app, "todos") == ["No date", label("a"), label("b")]

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["e", "enter"])
def test_edit_todo(store, inbox, key):
    async def check(app, pilot):
        await pilot.press("l", "j", key)
        assert app.screen.query_one(Input).value == "b"
        await type_in_prompt(pilot, "c", clear=True)
        assert highlighted(app) == label("c")

    run(KansoApp(store), check)
    assert texts(store, inbox.id) == ["a", "c"]


def test_blank_todo_text_is_refused(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "e", "ctrl+u", "enter")
        assert isinstance(app.screen, TextPrompt)
        assert notifications(app) == ["Text must not be blank"]

    run(KansoApp(store), check)
    assert texts(store, inbox.id) == ["a", "b"]


def test_space_toggles_done_and_autosorts(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "j", "space")
        assert prompts(app, "todos")[1:] == [label("b", done=True), label("a")]
        assert highlighted(app) == label("b", done=True)
        await pilot.press("space")
        assert prompts(app, "todos")[1:] == [label("a"), label("b")]
        await pilot.press("k", "space")

    run(KansoApp(store), check)
    assert [(t.done, t.completed_at) for t in store.todos(inbox.id)] == [(True, TODAY), (False, None)]


@pytest.mark.parametrize("key, days", [("full_stop", 1), ("comma", -1)])
def test_shift_date(store, inbox, key, days):
    async def check(app, pilot):
        await pilot.press("l", "j", key, key)
        assert highlighted(app) == label("b")

    run(KansoApp(store), check)
    assert [t.scheduled_at for t in store.todos(inbox.id)] == [TODAY, TODAY + timedelta(days=2 * days)]


@pytest.mark.parametrize(
    "start, shown, next_highlight",
    [(0, ["Tomorrow", "a", "c"], "a"), (1, ["Today", "b", "Tomorrow", "c"], "c"), (2, ["Today", "b", "Tomorrow", "a"], "a")],
)
def test_delete_highlights_next_or_previous_as_shown(store, dated, start, shown, next_highlight):
    async def check(app, pilot):
        await pilot.press("l", *["j"] * start, "d")
        assert prompts(app, "todos") == [s if len(s) > 1 else label(s) for s in shown]
        assert highlighted(app) == label(next_highlight)

    run(KansoApp(store), check)


def test_delete_last_todo(store):
    store.add_todo(store.add_list("Inbox").id, "a")

    async def check(app, pilot):
        await pilot.press("l", "d")
        assert prompts(app, "todos") == [] and highlighted(app) is None

    run(KansoApp(store), check)


# Labels


def test_edit_labels_prefilled_completed_and_saved(store, inbox):
    store.update_todo(store.todos(inbox.id)[0].id, labels=["home", "work"])

    async def check(app, pilot):
        await pilot.press("l", "number_sign")
        field = app.screen.query_one(Input)
        assert (field.value, field.placeholder) == ("home work ", "Labels")
        await pilot.press("escape", "j", "number_sign", "w")
        await pilot.pause()
        await pilot.press("tab", "enter")
        assert highlighted(app) == label("b") + "  #work "

    run(KansoApp(store), check)
    assert [t.labels for t in store.todos(inbox.id)] == [["home", "work"], ["work"]]


def test_invalid_label_is_reported_and_prompt_stays(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "number_sign", *"x" * 51, "enter")
        assert isinstance(app.screen, TextPrompt) and len(notifications(app)) == 1

    run(KansoApp(store), check)
    assert [t.labels for t in store.todos(inbox.id)] == [[], []]


# Repeats


async def open_repeats(pilot, *keys: str):
    await pilot.press("r", "l", *keys)


def test_add_repeat_spawns_todays_todo(store, repeating):
    async def check(app, pilot):
        await open_repeats(pilot, "a")
        assert isinstance(app.screen, RepeatDialog)
        await type_in_prompt(pilot, "Water")
        assert prompts(app, "repeats")[-1] == highlighted(app, "repeats") == "Water  Daily "
        assert app.focused.id == "repeats"
        await pilot.press("r")
        assert prompts(app, "todos")[-1] == label("Water ↻")

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["e", "enter"])
def test_edit_repeat(store, repeating, key):
    async def check(app, pilot):
        await open_repeats(pilot, "j", key)
        assert app.screen.text.value == "y"
        await type_in_prompt(pilot, "z", clear=True)
        assert highlighted(app, "repeats") == "z  Daily "

    run(KansoApp(store), check)
    assert [r.text for r in store.repeats(repeating.id)] == ["x", "z"]


@pytest.mark.parametrize("start, next_highlight", [(0, "y"), (1, "x")])
def test_delete_repeat_keeps_its_todos(store, repeating, start, next_highlight):
    async def check(app, pilot):
        await open_repeats(pilot, *["j"] * start, "d")
        assert prompts(app, "repeats") == [highlighted(app, "repeats")] == [f"{next_highlight}  Daily "]

    run(KansoApp(store), check)
    assert texts(store, repeating.id) == ["x", "y"]


def test_day_change_spawns_and_redraws(store, repeating, monkeypatch):
    class Tomorrow(date):
        @classmethod
        def today(cls):
            return TOMORROW

    for todo in store.todos(repeating.id):
        store.delete_todo(todo.id)

    async def check(app, pilot):
        assert prompts(app, "todos") == []  # starting doesn't recreate deleted occurrences
        app.check_day()
        assert prompts(app, "todos") == []
        monkeypatch.setattr(app_module, "date", Tomorrow)
        app.check_day()
        assert prompts(app, "todos") == ["Tomorrow", label("x ↻"), label("y ↻")]

    run(KansoApp(store), check)
