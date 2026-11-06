import asyncio
from datetime import date, timedelta

import pytest
from textual.widgets import ContentSwitcher, Input, OptionList, Static

from conftest import notifications, run, texts
from kanso import node as kanso_node
from kanso.core.store import NAME_MAX, Store
from kanso.node import Node
from kanso.sync import node as node_module
from kanso.sync.node import SyncNode, device_key, owner_lock
from kanso.sync.protocol import new_code
from kanso.sync.transport import MemoryTransport
from kanso.tui.app import KansoApp
from kanso.tui import dialogs
from kanso.tui.dialogs import ConfirmDialog, ConnectDialog, RepeatDialog, TextPrompt

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
YESTERDAY = TODAY - timedelta(days=1)


def tui(store, start_sync=None) -> KansoApp:
    return KansoApp(Node(store, start_sync))


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

    run(tui(store), check)


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

    run(tui(store), check)


def test_text_renders_plainly_not_as_markup(store):
    todo_list = store.add_list("[b]Lists[/b]")
    store.add_todo(todo_list.id, "[red]a[/red]", scheduled_at=None)

    async def check(app, pilot):
        def line(pane, y=0):
            return app.query_one(f"#{pane}").render_line(y).text.strip()

        assert (line("lists"), line("todos"), line("todos", 1)) == ("[b]Lists[/b]", "No date", "[ ] [red]a[/red]")

    run(tui(store), check)


@pytest.mark.parametrize("key", ["enter", "right", "l"])
def test_entering_a_list_focuses_its_first_todo(store, dated, key):
    async def check(app, pilot):
        await pilot.press(key)
        assert app.focused.id == "todos" and highlighted(app) == label("b")

    run(tui(store), check)


def test_entering_keeps_highlighted_todo(store, inbox):
    async def check(app, pilot):
        app.query_one("#todos", OptionList).highlighted = 2
        await pilot.press("l")
        assert highlighted(app) == label("b")

    run(tui(store), check)


def test_entering_empty_list_highlights_nothing(store):
    store.add_list("Empty")

    async def check(app, pilot):
        await pilot.press("l")
        assert app.focused.id == "todos" and highlighted(app) is None

    run(tui(store), check)


@pytest.mark.parametrize("key", ["left", "h", "escape"])
def test_leaving_a_pane_focuses_lists(store, inbox, key):
    async def check(app, pilot):
        for _ in ["todos", "repeats"]:
            await pilot.press("l", key)
            assert app.focused.id == "lists"
            await pilot.press("r")

    run(tui(store), check)


def test_r_toggles_panes_keeping_focus_side(store, repeating):
    async def check(app, pilot):
        switcher = app.query_one("#top", ContentSwitcher)
        await pilot.press("r")
        assert switcher.current == "repeats" and app.focused.id == "lists"
        assert prompts(app, "repeats") == ["x  Daily ", "y  Daily "]
        await pilot.press("l")
        assert app.focused.id == "repeats" and highlighted(app, "repeats") == "x  Daily "
        await pilot.press("r")
        assert switcher.current == "todos" and app.focused.id == "todos"

    run(tui(store), check)


def test_vim_movement(store):
    for name in ["Inbox", "Work", "Home"]:
        store.add_list(name)

    async def check(app, pilot):
        lists = app.query_one("#lists", OptionList)
        for key, expected in [("j", 1), ("j", 2), ("k", 1), ("g", 0), ("G", 2)]:
            await pilot.press(key)
            assert lists.highlighted == expected

    run(tui(store), check)


def test_cursor_skips_date_titles(store, dated):
    async def check(app, pilot):
        assert prompts(app, "todos") == ["Today", label("b"), "Tomorrow", label("a"), label("c")]
        await pilot.press("l", "j")
        assert highlighted(app) == label("a")
        await pilot.press("k", "k", "g")
        assert highlighted(app) == label("b")

    run(tui(store), check)


# Keys act only where they apply


@pytest.mark.parametrize("key", ["space", "number_sign", "full_stop", "comma", "y"])
def test_todo_keys_do_nothing_elsewhere(store, inbox, key):
    async def check(app, pilot):
        await pilot.press(key)  # lists focused
        await pilot.press("r", "l", key)  # repeats focused
        await pilot.press("r")
        app.query_one("#todos", OptionList).highlighted = None
        await pilot.press(key)  # todos focused, nothing highlighted
        assert on_main_screen(app)
        assert prompts(app, "todos") == ["Today", label("a"), label("b")]

    run(tui(store), check)


@pytest.mark.parametrize(
    "pane, key", [(p, k) for p in ("lists", "todos", "repeats", "days", "habits") for k in "ed"] + [("days", "space"), ("days", "x")]
)
def test_edit_delete_and_mark_need_a_highlight(store, pane, key):
    async def check(app, pilot):
        if pane in ("repeats", "habits"):
            app.show_pane(pane)
        app.query_one(f"#{pane}").focus()
        await pilot.press(key)
        assert on_main_screen(app) and not notifications(app)

    run(tui(store), check)


@pytest.mark.parametrize("pane", ["todos", "repeats", "days", "habits"])
def test_add_needs_a_list(store, pane):
    async def check(app, pilot):
        if pane != "todos":
            app.show_pane(pane)
        app.query_one(f"#{pane}").focus()
        await pilot.press("a")
        assert on_main_screen(app)

    run(tui(store), check)


def test_keys_type_into_prompts(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "a", *"aedhjklgGr#", "space", "full_stop", "comma", "enter")
        assert app.focused.id == "todos"

    run(tui(store), check)
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

    run(tui(store), check)


def test_rename_list(store, inbox):
    async def check(app, pilot):
        await pilot.press("e")
        assert app.screen.query_one(Input).value == "Inbox"
        await type_in_prompt(pilot, "Job", clear=True)
        assert prompts(app, "lists") == ["Job"] and prompts(app, "todos")[1:] == [label("a"), label("b")]

    run(tui(store), check)


@pytest.mark.parametrize("yes", ["y", "enter"])
def test_delete_list_after_confirming_shows_the_next(store, inbox, yes):
    store.add_repeat(inbox.id, "r")
    work = store.add_list("Work")
    store.add_todo(work.id, "report")

    async def check(app, pilot):
        await pilot.press("d")
        question = str(app.screen.query_one("#question", Static).render())
        assert question == 'Delete list "Inbox" with its 3 todos and 1 repeat?'
        await pilot.press(yes)
        assert prompts(app, "lists") == ["Work"] and highlighted(app, "lists") == "Work"
        assert prompts(app, "todos") == ["Today", label("report")]

    run(tui(store), check)
    assert [l.name for l in store.lists()] == ["Work"]


def test_deleting_the_last_list_empties_the_panes(store):
    store.add_repeat(store.add_list("Inbox").id, "r")

    async def check(app, pilot):
        await pilot.press("d", "y")
        assert prompts(app, "lists") == prompts(app, "todos") == []
        await pilot.press("r")
        assert prompts(app, "repeats") == []

    run(tui(store), check)
    assert store.lists() == []


def test_first_list_then_todo(store):
    async def check(app, pilot):
        await pilot.press("a")
        await type_in_prompt(pilot, "Inbox")
        await pilot.press("l", "a")
        await type_in_prompt(pilot, "milk")
        assert prompts(app, "todos") == ["Today", label("milk")]

    run(tui(store), check)


# Todos


def test_add_without_highlight_appends_for_today(store, dated):
    async def check(app, pilot):
        app.query_one("#todos").focus()
        await pilot.press("a")
        assert app.screen.query_one(Input).max_length == 0
        await type_in_prompt(pilot, "new")
        assert prompts(app, "todos") == ["Today", label("b"), label("new"), "Tomorrow", label("a"), label("c")]
        assert highlighted(app) == label("new")

    run(tui(store), check)


def test_add_inserts_below_highlighted_on_its_date(store, dated):
    async def check(app, pilot):
        await pilot.press("l", "j", "a")
        await type_in_prompt(pilot, "new")
        assert prompts(app, "todos")[3:] == [label("a"), label("new"), label("c")]

    run(tui(store), check)
    assert texts(store, dated.id) == ["a", "new", "b", "c"]


def test_add_below_unscheduled_stays_unscheduled(store):
    store.add_todo(store.add_list("Inbox").id, "a", scheduled_at=None)

    async def check(app, pilot):
        await pilot.press("l", "a")
        await type_in_prompt(pilot, "b")
        assert prompts(app, "todos") == ["No date", label("a"), label("b")]

    run(tui(store), check)


@pytest.mark.parametrize("key", ["e", "enter"])
def test_edit_todo(store, inbox, key):
    async def check(app, pilot):
        await pilot.press("l", "j", key)
        assert app.screen.query_one(Input).value == "b"
        await type_in_prompt(pilot, "c", clear=True)
        assert highlighted(app) == label("c")

    run(tui(store), check)
    assert texts(store, inbox.id) == ["a", "c"]


def test_blank_todo_text_is_refused(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "e", "ctrl+u", "enter")
        assert isinstance(app.screen, TextPrompt)
        assert notifications(app) == ["Text must not be blank"]

    run(tui(store), check)
    assert texts(store, inbox.id) == ["a", "b"]


@pytest.fixture
def old_done(store):
    """Inbox with "old" done yesterday, "late" scheduled yesterday but done today, and "open"."""
    todo_list = store.add_list("Inbox")
    for text in ["old", "late"]:
        todo = store.add_todo(todo_list.id, text, scheduled_at=YESTERDAY, done=True)
    store.add_todo(todo_list.id, "open")
    old = store.todos(todo_list.id)[0]
    old.completed_at = YESTERDAY
    store._save(old)
    return todo_list


def test_todos_done_before_today_are_hidden_until_f(store, old_done):
    shown = ["Yesterday", label("old", done=True), label("late", done=True), "Today", label("open")]
    hidden = ["Yesterday", label("late", done=True), "Today", label("open"), "1 todo done before today hidden · f shows"]

    async def check(app, pilot):
        assert prompts(app, "todos") == hidden
        await pilot.press("l", "f")
        assert prompts(app, "todos") == shown and highlighted(app) == label("late", done=True)
        await pilot.press("k", "f")  # "old" is hidden away from under the highlight
        assert prompts(app, "todos") == hidden and highlighted(app) == label("late", done=True)
        await pilot.press("f")

    run(tui(store), check)
    assert store.meta("show_old_done") == "1"

    async def remembered(app, pilot):
        assert prompts(app, "todos") == shown

    run(tui(store), remembered)


def test_add_below_a_todo_counts_hidden_ones(store, old_done):
    async def check(app, pilot):
        await pilot.press("l", "a")
        await type_in_prompt(pilot, "new")
        await pilot.press("f")
        assert prompts(app, "todos")[1:4] == [label("old", done=True), label("late", done=True), label("new")]

    run(tui(store), check)
    assert texts(store, old_done.id) == ["old", "late", "new", "open"]


def test_highlight_of_a_hidden_todo_is_dropped_on_redraw(store, old_done):
    async def check(app, pilot):
        await pilot.press("l", "f", "k")
        assert highlighted(app) == label("old", done=True)
        store.set_meta("show_old_done", None)  # as another kanso window on this data dir would
        pane = app.query_one("#todos")
        pane.show(pane.current)
        assert highlighted(app) is None

    run(tui(store), check)


def test_space_toggles_done_last_of_the_done_and_reopens_first_of_the_open(store, inbox):
    store.add_todo(inbox.id, "c")

    def shown(app):
        return prompts(app, "todos")[1:]

    async def check(app, pilot):
        await pilot.press("l", "j", "space")
        assert shown(app) == [label("b", done=True), label("a"), label("c")]
        assert highlighted(app) == label("b", done=True)
        await pilot.press("j", "space")
        assert shown(app) == [label("b", done=True), label("a", done=True), label("c")]
        await pilot.press("k", "space")
        assert shown(app) == [label("a", done=True), label("b"), label("c")]
        assert highlighted(app) == label("b")

    run(tui(store), check)
    assert [(t.text, t.done, t.completed_at) for t in store.todos(inbox.id)] == [
        ("b", False, None),
        ("c", False, None),
        ("a", True, TODAY),
    ]


@pytest.fixture
def grouped(store):
    """Today: done d, e then open a, b, c; tomorrow: open t, u."""
    todo_list = store.add_list("Inbox")
    for text in "abcde":
        store.add_todo(todo_list.id, text, done=text in "de")
    for text in "tu":
        store.add_todo(todo_list.id, text, scheduled_at=TOMORROW)
    return todo_list


def shown_todos(app) -> list[str]:
    """Each todo's text, done ones in capitals, and date titles as they are."""
    return [p[4:].upper() if p.startswith("[x]") else p[4:] if p.startswith("[ ]") else p for p in prompts(app, "todos")]


@pytest.mark.parametrize("down, up", [("J", "K"), ("shift+down", "shift+up")])
def test_move_reorders_within_its_group(store, grouped, down, up):
    async def check(app, pilot):
        assert shown_todos(app) == ["Today", "D", "E", "a", "b", "c", "Tomorrow", "t", "u"]
        await pilot.press("l", "j", "j", down)  # a
        assert shown_todos(app)[3:6] == ["b", "a", "c"] and highlighted(app) == label("a")
        await pilot.press(down, down)  # already last of the open: stays
        assert shown_todos(app)[3:6] == ["b", "c", "a"]
        await pilot.press(up, up, up)  # first of the open: stays, not among the done
        assert shown_todos(app) == ["Today", "D", "E", "a", "b", "c", "Tomorrow", "t", "u"]
        await pilot.press("g", down)  # d, among the done only
        assert shown_todos(app)[1:6] == ["E", "D", "a", "b", "c"]
        await pilot.press("G", up)  # u, within tomorrow
        assert shown_todos(app)[6:] == ["Tomorrow", "u", "t"]

    run(tui(store), check)
    assert "".join(texts(store, grouped.id)) == "abcedut"


def test_move_lists_keeping_what_is_shown(store, inbox):
    store.add_todo(store.add_list("Work").id, "report")

    async def check(app, pilot):
        await pilot.press("J")
        assert prompts(app, "lists") == ["Work", "Inbox"] and highlighted(app, "lists") == "Inbox"
        assert prompts(app, "todos") == ["Today", label("a"), label("b")]
        await pilot.press("J", "K", "K")  # last stays last, first stays first
        assert prompts(app, "lists") == ["Inbox", "Work"]

    run(tui(store), check)
    assert [l.name for l in store.lists()] == ["Inbox", "Work"]


def test_move_repeats(store, repeating):
    store.add_repeat(repeating.id, "z")

    async def check(app, pilot):
        await open_repeats(pilot, "J", "J")
        assert prompts(app, "repeats") == ["y  Daily ", "z  Daily ", "x  Daily "]
        await pilot.press("shift+up")
        assert prompts(app, "repeats") == ["y  Daily ", "x  Daily ", "z  Daily "]
        assert highlighted(app, "repeats") == "x  Daily "

    run(tui(store), check)
    assert [r.text for r in store.repeats(repeating.id)] == ["y", "x", "z"]


@pytest.mark.parametrize("pane", ["lists", "todos", "repeats"])
def test_move_needs_a_highlight(store, pane):
    store.add_repeat(store.add_list("Inbox").id, "x")

    async def check(app, pilot):
        if pane == "repeats":
            await pilot.press("r")
        widget = app.query_one(f"#{pane}", OptionList)
        widget.focus()
        widget.highlighted = None
        await pilot.press("J", "K")
        assert on_main_screen(app)

    run(tui(store), check)


@pytest.mark.parametrize("key, days", [("full_stop", 1), ("comma", -1)])
def test_shift_date(store, inbox, key, days):
    async def check(app, pilot):
        await pilot.press("l", "j", key, key)
        assert highlighted(app) == label("b")

    run(tui(store), check)
    assert [t.scheduled_at for t in store.todos(inbox.id)] == [TODAY, TODAY + timedelta(days=2 * days)]


@pytest.mark.parametrize(
    "start, shown, next_highlight",
    [(0, ["Tomorrow", "a", "c"], "a"), (1, ["Today", "b", "Tomorrow", "c"], "c"), (2, ["Today", "b", "Tomorrow", "a"], "a")],
)
def test_delete_highlights_next_or_previous_as_shown(store, dated, start, shown, next_highlight):
    async def check(app, pilot):
        await pilot.press("l", *["j"] * start, "d", "y")
        assert prompts(app, "todos") == [s if len(s) > 1 else label(s) for s in shown]
        assert highlighted(app) == label(next_highlight)

    run(tui(store), check)


def test_delete_last_todo(store):
    store.add_todo(store.add_list("Inbox").id, "a")

    async def check(app, pilot):
        await pilot.press("l", "d", "y")
        assert prompts(app, "todos") == [] and highlighted(app) is None

    run(tui(store), check)


@pytest.mark.parametrize("pane, question", [("todos", 'Delete "a"?'), ("repeats", 'Delete repeat "x"? The todos it added stay.')])
@pytest.mark.parametrize("no", ["n", "escape"])
def test_deleting_needs_confirmation(store, repeating, pane, question, no):
    store.add_todo(repeating.id, "a", index=0)

    async def check(app, pilot):
        if pane == "repeats":
            await pilot.press("r")
        await pilot.press("l", "d")
        assert isinstance(app.screen, ConfirmDialog)
        assert str(app.screen.query_one("#question", Static).render()) == question
        await pilot.press(no)
        assert on_main_screen(app) and app.focused.id == pane

    run(tui(store), check)
    assert texts(store, repeating.id) == ["a", "x", "y"] and len(store.repeats(repeating.id)) == 2


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

    run(tui(store), check)
    assert [t.labels for t in store.todos(inbox.id)] == [["home", "work"], ["work"]]


def test_duplicate_copies_below_and_highlights_the_copy(store, dated):
    store.update_todo(store.todos(dated.id)[0].id, labels=["home"])

    async def check(app, pilot):
        await pilot.press("l", "j", "y")
        assert prompts(app, "todos")[3:] == [label("a") + "  #home ", label("a") + "  #home ", label("c")]
        assert app.query_one("#todos", OptionList).highlighted == 4

    run(tui(store), check)
    assert texts(store, dated.id) == ["a", "a", "b", "c"]


def test_duplicating_a_repeating_todo_is_refused(store, repeating):
    async def check(app, pilot):
        await pilot.press("l", "y")
        assert on_main_screen(app) and len(notifications(app)) == 1

    run(tui(store), check)
    assert texts(store, repeating.id) == ["x", "y"]


def test_invalid_label_is_reported_and_prompt_stays(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "number_sign", *"x" * 51, "enter")
        assert isinstance(app.screen, TextPrompt) and len(notifications(app)) == 1

    run(tui(store), check)
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

    run(tui(store), check)


@pytest.mark.parametrize("key", ["e", "enter"])
def test_edit_repeat(store, repeating, key):
    async def check(app, pilot):
        await open_repeats(pilot, "j", key)
        assert app.screen.text.value == "y"
        await type_in_prompt(pilot, "z", clear=True)
        assert highlighted(app, "repeats") == "z  Daily "

    run(tui(store), check)
    assert [r.text for r in store.repeats(repeating.id)] == ["x", "z"]


@pytest.mark.parametrize("key", ["e", "enter"])
def test_editing_a_repeats_todo_edits_the_repeat(store, repeating, key):
    async def check(app, pilot):
        await pilot.press("l", "j", key)
        assert app.query_one("#top", ContentSwitcher).current == "repeats"
        assert isinstance(app.screen, RepeatDialog) and app.screen.text.value == "y"
        await type_in_prompt(pilot, "z", clear=True)
        assert app.focused.id == "repeats" and highlighted(app, "repeats") == "z  Daily "
        await pilot.press("r")
        assert prompts(app, "todos")[1:] == [label("x ↻"), label("z ↻")]

    run(tui(store), check)
    assert texts(store, repeating.id) == ["x", "z"]


@pytest.mark.parametrize("start, next_highlight", [(0, "y"), (1, "x")])
def test_delete_repeat_keeps_its_todos(store, repeating, start, next_highlight):
    async def check(app, pilot):
        await open_repeats(pilot, *["j"] * start, "d", "y")
        assert prompts(app, "repeats") == [highlighted(app, "repeats")] == [f"{next_highlight}  Daily "]

    run(tui(store), check)
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
        app.node.check_day()
        assert prompts(app, "todos") == []
        monkeypatch.setattr(kanso_node, "date", Tomorrow)
        app.node.check_day()
        assert prompts(app, "todos") == ["Tomorrow", label("x ↻"), label("y ↻")]

    run(tui(store), check)


# Sync


@pytest.fixture
def network(monkeypatch):
    """An in-memory network, with nodes polling for changes often, though not so often
    that the app never looks idle to the test pilot, which then waits after each key."""
    monkeypatch.setattr(node_module, "POLL", 0.2)
    return {}


def sync_over(network: dict):
    """A `start_node` for the app on the in-memory `network`, as key-app."""

    async def start(store, on_update):
        return SyncNode(store, MemoryTransport("key-app", network), "app", on_update)

    return start


@pytest.fixture
def remote(tmp_path, network):
    """Another device, b, on the network; run it with `b.run()` inside the app's loop."""
    (tmp_path / "b").mkdir()
    store = Store(tmp_path / "b" / "kanso.db")
    yield SyncNode(store, MemoryTransport("key-b", network), "b")
    store.close()


async def eventually(condition, timeout: float = 2):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def dialog_text(app, id: str) -> str:
    return str(app.screen.query_one(f"#{id}", Static).render())


def test_without_sync_connect_shows_id_and_code_only(store):
    async def check(app, pilot):
        await pilot.press("c")
        assert isinstance(app.screen, ConnectDialog)
        assert dialog_text(app, "key") == device_key(store)
        assert dialog_text(app, "status") == "Sync is off"
        assert not app.screen.query(Input)
        await pilot.press("escape")
        assert on_main_screen(app)

    run(tui(store), check)
    assert store.meta("pair_code")


def test_a_second_window_leaves_sync_to_the_first(store, network):
    lock = owner_lock(store)

    async def check(app, pilot):
        assert app.node.sync is None
        await pilot.press("c")
        assert "another kanso window" in dialog_text(app, "status")

    run(tui(store, sync_over(network)), check)
    lock.close()


def test_connect_pairs_and_remote_changes_appear_keeping_the_highlight(store, inbox, network, remote):
    async def check(app, pilot):
        asyncio.create_task(remote.run())
        await eventually(lambda: remote.store.is_trusted("key-b"))
        code = new_code(remote.store)
        await pilot.press("l", "j", "c")
        await pilot.press(*"key-b", "enter", *code, "enter")
        await eventually(lambda: on_main_screen(app))
        assert "Connected to b" in notifications(app)
        assert store.is_trusted("key-b") and remote.store.is_trusted("key-app")
        await eventually(lambda: app.sub_title.startswith("1 device online · synced "))
        remote.store.add_todo(remote.store.lists()[0].id, "from b")
        await eventually(lambda: label("from b") in prompts(app, "todos"))
        assert highlighted(app) == label("b") and app.focused.id == "todos"
        await remote.transport.close()

    run(tui(store, sync_over(network)), check)
    lock = owner_lock(store)  # released on exit
    assert lock is not None
    lock.close()


def test_failed_connect_is_reported_and_dialog_stays(store, network, remote):
    async def check(app, pilot):
        asyncio.create_task(remote.run())
        await pilot.press("c", *"key-b", "enter", *"WRONG-CODE0", "enter")
        await eventually(lambda: notifications(app))
        assert "Wrong or expired code" in notifications(app)[0]
        assert isinstance(app.screen, ConnectDialog) and dialog_text(app, "status") == ""
        app.screen.peer.focus()
        await pilot.press("ctrl+u", *"key-app", "enter")
        assert "own ID" in notifications(app)[-1]
        await remote.transport.close()

    run(tui(store, sync_over(network)), check)


@pytest.mark.parametrize("tool, message", [(["true"], "Copied"), (None, "terminal's clipboard")])
def test_ctrl_y_copies_id_and_code(store, monkeypatch, tool, message):
    monkeypatch.setattr(dialogs, "clipboard_command", lambda: tool)

    async def check(app, pilot):
        await pilot.press("c", "ctrl+y")
        assert app.clipboard == f"{device_key(store)} {app.screen.code}"
        assert message in notifications(app)[-1]

    run(tui(store), check)


def test_remote_deletion_of_the_shown_list_empties_the_panes(store, inbox, network, remote):
    async def check(app, pilot):
        asyncio.create_task(remote.run())
        await app.node.pair("key-b", new_code(remote.store))
        await pilot.press("l")
        remote.store.delete_list(inbox.id)
        await eventually(lambda: prompts(app, "lists") == [])
        assert prompts(app, "todos") == []
        await remote.transport.close()

    run(tui(store, sync_over(network)), check)


# Habits


def start_habit(store, todo_list, text: str, days_ago: int):
    """A daily habit started `days_ago`, with its days backfilled."""
    habit = store.add_habit(todo_list.id, text)
    habit.start = TODAY - timedelta(days=days_ago)
    store._save(habit)
    store.spawn()
    return habit


@pytest.fixture
def habitual(store):
    """Inbox with todo "a", and daily habits Read (for 10 days) and Run (since yesterday)."""
    todo_list = store.add_list("Inbox")
    store.add_todo(todo_list.id, "a")
    start_habit(store, todo_list, "Read", 9)
    start_habit(store, todo_list, "Run", 1)
    return todo_list


def mark(state=None) -> str:
    return {None: "[ ]", "success": "[✓]", "failure": "[✗]"}[state]


def focus_day(app, prompt: str, nth: int = 0):
    """Focus the habit days, highlighting the `nth` option reading `prompt`."""
    days = app.query_one("#days", OptionList)
    days.focus()
    days.highlighted = [i for i, p in enumerate(prompts(app, "days")) if p == prompt][nth]


def states(store, todo_list) -> list[tuple[str, int, str | None]]:
    return [(d.title, (TODAY - d.day).days, d.state) for d in store.habit_days(todo_list.id)]


def test_habit_days_show_below_todos_for_the_last_week(store, habitual):
    async def check(app, pilot):
        assert prompts(app, "todos") == ["Today", label("a")]
        days = prompts(app, "days")
        assert days[:6] == ["Today", f"{mark()} Read", f"{mark()} Run", "Yesterday", f"{mark()} Read", f"{mark()} Run"]
        assert days.count(f"{mark()} Read") == 7 and days[-1] == "3 older days hidden · f shows"
        await pilot.press("l", "tab")
        assert app.focused.id == "days"
        await pilot.press("f")
        assert prompts(app, "days").count(f"{mark()} Read") == 10
        await pilot.press("f")
        assert prompts(app, "days")[-1] == "3 older days hidden · f shows"

    run(tui(store), check)


def test_todos_and_habits_have_titles(store):
    async def check(app, pilot):
        titles = list(app.query(".title").results(Static))
        assert [str(t.render()) for t in titles] == ["TODOS", "HABITS"]
        for title, side in zip(titles, ("#top", "#bottom")):
            assert title.region.bottom == app.query_one(side).region.y  # right above its side

    run(tui(store), check)


def test_marking_past_days_and_clearing(store, habitual):
    async def check(app, pilot):
        focus_day(app, f"{mark()} Read", 1)  # yesterday's
        await pilot.press("space")
        assert highlighted(app, "days") == f"{mark('success')} Read"
        await pilot.press("x")
        assert highlighted(app, "days") == f"{mark('failure')} Read"
        await pilot.press("x")
        assert highlighted(app, "days") == f"{mark()} Read"
        await pilot.press("j", "space")
        assert highlighted(app, "days") == f"{mark('success')} Run"

    run(tui(store), check)
    assert states(store, habitual)[2:4] == [("Read", 1, None), ("Run", 1, "success")]


def test_today_cant_be_marked(store, habitual):
    async def check(app, pilot):
        focus_day(app, f"{mark()} Read")
        await pilot.press("space")
        assert notifications(app) == ["Habits can only be marked once the day is over"]
        assert highlighted(app, "days") == f"{mark()} Read"

    run(tui(store), check)


def test_r_switches_the_focused_side(store, habitual):
    async def check(app, pilot):
        top, bottom = (app.query_one(f"#{s}", ContentSwitcher) for s in ("top", "bottom"))
        focus_day(app, f"{mark()} Read")
        await pilot.press("r")
        assert (top.current, bottom.current, app.focused.id) == ("todos", "habits", "habits")
        assert prompts(app, "habits") == ["Read  Daily ", "Run  Daily "]
        await pilot.press("r")
        assert (bottom.current, app.focused.id) == ("days", "days")
        await pilot.press("h", "r")  # from the lists: the top side
        assert (top.current, bottom.current, app.focused.id) == ("repeats", "days", "lists")

    run(tui(store), check)


@pytest.mark.parametrize("key", ["e", "enter"])
def test_editing_a_day_edits_its_habit(store, habitual, key):
    async def check(app, pilot):
        focus_day(app, f"{mark()} Run")
        await pilot.press(key)
        assert isinstance(app.screen, RepeatDialog) and app.screen.text.value == "Run"
        assert app.query_one("#bottom", ContentSwitcher).current == "habits"
        await type_in_prompt(pilot, "Jog", clear=True)
        await pilot.press("r")
        assert prompts(app, "days")[2] == f"{mark()} Jog"

    run(tui(store), check)


def test_adding_from_the_days_switches_to_the_habits(store, habitual):
    async def check(app, pilot):
        focus_day(app, f"{mark()} Read")
        await pilot.press("a")
        assert isinstance(app.screen, RepeatDialog) and app.screen.text.placeholder == "Habit"
        assert app.query_one("#bottom", ContentSwitcher).current == "habits"
        await type_in_prompt(pilot, "Stretch")
        assert app.focused.id == "habits" and highlighted(app, "habits") == "Stretch  Daily "

    run(tui(store), check)
    assert [h.text for h in store.habits(habitual.id)] == ["Read", "Run", "Stretch"]


def test_add_habit_spawns_its_day_and_moves(store, habitual):
    async def check(app, pilot):
        focus_day(app, f"{mark()} Read")
        await pilot.press("r", "a")
        assert app.screen.text.placeholder == "Habit"
        await type_in_prompt(pilot, "Stretch")
        assert prompts(app, "habits")[-1] == highlighted(app, "habits") == "Stretch  Daily "
        await pilot.press("K")
        assert prompts(app, "habits") == ["Read  Daily ", "Stretch  Daily ", "Run  Daily "]
        await pilot.press("r")
        assert prompts(app, "days")[1:4] == [f"{mark()} Read", f"{mark()} Stretch", f"{mark()} Run"]
        await pilot.press("J")  # days follow their habits' order; they don't move
        assert prompts(app, "days")[1:4] == [f"{mark()} Read", f"{mark()} Stretch", f"{mark()} Run"]

    run(tui(store), check)


def test_deleting_a_habit_keeps_its_days(store, habitual):
    async def check(app, pilot):
        focus_day(app, f"{mark()} Read")
        await pilot.press("r", "j", "d")
        assert str(app.screen.query_one("#question", Static).render()) == 'Delete habit "Run"? Its days stay.'
        await pilot.press("y")
        assert prompts(app, "habits") == ["Read  Daily "]
        await pilot.press("r")
        focus_day(app, f"{mark()} Run")
        await pilot.press("e")
        assert notifications(app) == ["Its habit was deleted"]

    run(tui(store), check)
    assert [t for t, _, _ in states(store, habitual)].count("Run") == 2


def test_deleting_a_day(store, habitual):
    async def check(app, pilot):
        focus_day(app, f"{mark()} Run", 1)
        await pilot.press("d")
        assert str(app.screen.query_one("#question", Static).render()) == 'Delete "Run" of Yesterday?'
        await pilot.press("y")
        assert prompts(app, "days")[3:5] == ["Yesterday", f"{mark()} Read"]

    run(tui(store), check)
    assert ("Run", 1, None) not in states(store, habitual)


def test_deleting_a_list_counts_its_habits(store, habitual):
    async def check(app, pilot):
        await pilot.press("d")
        question = str(app.screen.query_one("#question", Static).render())
        assert question == 'Delete list "Inbox" with its 1 todo and 2 habits?'
        await pilot.press("y")
        assert prompts(app, "days") == []

    run(tui(store), check)


def test_a_new_day_spawns_habit_days(store, habitual, monkeypatch):
    class Tomorrow(date):
        @classmethod
        def today(cls):
            return TOMORROW

    async def check(app, pilot):
        monkeypatch.setattr(kanso_node, "date", Tomorrow)
        app.node.check_day()
        assert prompts(app, "days")[:3] == ["Tomorrow", f"{mark()} Read", f"{mark()} Run"]

    run(tui(store), check)


def test_remote_habit_changes_appear(store, habitual, network, remote):
    async def check(app, pilot):
        asyncio.create_task(remote.run())
        await app.node.pair("key-b", new_code(remote.store))
        await eventually(lambda: remote.store.lists())  # b merges our rows after we're done
        [run_habit] = [h for h in remote.store.habits(habitual.id) if h.text == "Run"]
        remote.store.update_habit(run_habit.id, text="Jog")
        await eventually(lambda: f"{mark()} Jog" in prompts(app, "days"))
        await remote.transport.close()

    run(tui(store, sync_over(network)), check)


# Ukrainian layout: the same physical keys work


def test_ukrainian_layout_keys_act_like_us_ones(store, inbox):
    store.add_list("Work")

    async def check(app, pilot):
        await pilot.press("о")  # j
        assert highlighted(app, "lists") == "Work"
        await pilot.press("Л")  # K: move up
        assert prompts(app, "lists") == ["Work", "Inbox"]
        await pilot.press("л", "ф")  # k, a: add a list
        assert isinstance(app.screen, TextPrompt)
        await pilot.press("escape", "д", "о")  # l, j: into the todos, down
        assert highlighted(app) == label("b")
        await pilot.press("ю")  # .: +1 day
        assert [t.scheduled_at for t in store.todos(inbox.id)] == [TODAY, TOMORROW]
        await pilot.press("numero_sign")  # #: labels
        assert app.screen.query_one(Input).placeholder == "Labels"
        await pilot.press("escape", "к")  # r: repeats
        assert app.query_one("#top", ContentSwitcher).current == "repeats"

    run(tui(store), check)


@pytest.mark.parametrize("key, deleted", [("н", True), ("т", False)])
def test_ukrainian_layout_answers_confirmations(store, inbox, key, deleted):
    async def check(app, pilot):
        await pilot.press("д", "в", key)  # l, d, then y or n
        assert on_main_screen(app)

    run(tui(store), check)
    assert texts(store, inbox.id) == (["b"] if deleted else ["a", "b"])


def test_ukrainian_layout_keys_stay_out_of_the_footer_and_type_into_prompts(store, inbox):
    async def check(app, pilot):
        await pilot.press("l")
        shown = [b.binding.key for b in app.active_bindings.values() if b.binding.show]
        assert shown and all(key.isascii() for key in shown)
        await pilot.press("ф", *"молоко", "enter")

    run(tui(store), check)
    assert texts(store, inbox.id) == ["a", "молоко", "b"]
