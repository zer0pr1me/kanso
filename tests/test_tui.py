import asyncio
from datetime import date, timedelta

import pytest
from textual.widgets import ContentSwitcher, Input, OptionList, RadioSet, SelectionList

from kanso.core.store import NAME_MAX, Store
from kanso.tui import app as app_module
from kanso.tui.app import (
    LABEL_STYLE,
    RULE_STYLE,
    KansoApp,
    LabelSuggester,
    RepeatDialog,
    describe_rule,
    human_date,
    repeat_label,
    section_title,
    sections,
    todo_label,
)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "kanso.db")
    yield s
    s.close()


def label(text: str) -> str:
    """Label of an undone todo."""
    return f"[ ] {text}"


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
    assert str(todo_label(todo)) == "[ ] Buy milk"
    todo = store.update_todo(todo.id, done=True)
    assert str(todo_label(todo)) == "[x] Buy milk"


def test_section_title():
    assert section_title(date.today()) == "Today"
    assert section_title(date(2020, 3, 7)) == "Mar 7, 2020"
    assert section_title(None) == "No date"


def test_sections_by_date_unscheduled_last_in_list_order(store):
    todo_list = store.add_list("Inbox")
    early, late = date(2026, 1, 1), date(2026, 1, 2)
    for text, day in [("a", late), ("b", None), ("c", early), ("d", late), ("e", None)]:
        store.add_todo(todo_list.id, text, scheduled_at=day)
    grouped = [(day, [t.text for t in todos]) for day, todos in sections(store.todos(todo_list.id))]
    assert grouped == [(early, ["c"]), (late, ["a", "d"]), (None, ["b", "e"])]
    assert sections([]) == []


def test_sections_put_done_first_in_list_order(store):
    todo_list = store.add_list("Inbox")
    for text in "abcd":
        store.add_todo(todo_list.id, text, done=text in "bd")
    store.add_todo(todo_list.id, "e", done=True, scheduled_at=None)
    grouped = [(day, [t.text for t in todos]) for day, todos in sections(store.todos(todo_list.id))]
    assert grouped == [(date.today(), ["b", "d", "a", "c"]), (None, ["e"])]


WEDNESDAY = date(2026, 10, 21)


@pytest.mark.parametrize(
    "day, expected",
    [
        (date(2026, 10, 21), "Today"),
        (date(2026, 10, 22), "Tomorrow"),
        (date(2026, 10, 20), "Yesterday"),
        (date(2026, 10, 19), "Monday"),
        (date(2026, 10, 23), "Friday"),
        (date(2026, 10, 25), "Sunday"),
        (date(2026, 10, 26), "Next Monday"),
        (date(2026, 11, 1), "Next Sunday"),
        (date(2026, 10, 18), "Last Sunday"),
        (date(2026, 10, 12), "Last Monday"),
        (date(2026, 11, 2), "Nov 2"),
        (date(2026, 10, 11), "Oct 11"),
        (date(2027, 1, 5), "Jan 5, 2027"),
        (date(2025, 12, 31), "Dec 31, 2025"),
    ],
)
def test_human_date(day, expected):
    assert human_date(day, WEDNESDAY) == expected


@pytest.mark.parametrize(
    "today, day, expected",
    [
        (date(2026, 10, 19), date(2026, 10, 18), "Yesterday"),  # Monday: yesterday was last week
        (date(2026, 10, 25), date(2026, 10, 26), "Tomorrow"),  # Sunday: tomorrow is next week
        (date(2026, 10, 25), date(2026, 10, 19), "Monday"),
        (date(2026, 10, 25), date(2026, 11, 1), "Next Sunday"),
        (date(2026, 10, 25), date(2026, 11, 2), "Nov 2"),
        (date(2026, 12, 31), date(2027, 1, 4), "Next Monday"),  # across a year boundary
        (date(2026, 12, 31), date(2027, 1, 11), "Jan 11, 2027"),
    ],
)
def test_human_date_at_week_edges(today, day, expected):
    assert human_date(day, today) == expected


def test_human_date_defaults_to_today():
    assert human_date(date.today()) == "Today"


def test_labels_render_as_plain_text_not_markup(store):
    todo_list = store.add_list("[b]Lists[/b]")
    store.add_todo(todo_list.id, "[red]a[/red]", done=True, scheduled_at=None)

    async def check(app, pilot):
        rendered = lambda id, y=0: app.query_one(f"#{id}").render_line(y).text.strip()
        assert rendered("lists") == "[b]Lists[/b]"
        assert rendered("todos") == "No date"
        assert rendered("todos", 1) == "[x] [red]a[/red]"

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
        assert prompts(app, "todos") == ["Today", label("a"), label("b")]

    run(KansoApp(store), check)


def test_highlighting_a_list_shows_its_todos(store):
    inbox, work = store.add_list("Inbox"), store.add_list("Work")
    store.add_todo(inbox.id, "a")
    store.add_todo(work.id, "c")

    async def check(app, pilot):
        app.query_one("#lists", OptionList).focus()
        await pilot.press("down")
        assert prompts(app, "todos") == ["Today", label("c")]
        await pilot.press("up")
        assert prompts(app, "todos") == ["Today", label("a")]

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
        app.query_one("#todos").focus()
        await pilot.press("a")
        await type_in_prompt(pilot, "new")
        assert prompts(app, "todos") == ["Today", label("a"), label("b"), label("new")]
        assert highlighted(app) == label("new")
        assert app.focused.id == "todos"

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "b", "new"]


def test_add_inserts_below_highlighted(store, inbox):
    async def check(app, pilot):
        await pilot.press("l")
        await pilot.press("a")
        await type_in_prompt(pilot, "new")
        assert highlighted(app) == label("new")

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "new", "b"]


def test_add_strips_text(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "a", "space", "n", "space", "enter")

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "n", "b"]


@pytest.mark.parametrize("keys", [["escape"], ["space", "enter"], ["enter"]])
def test_add_cancelled_or_blank_does_nothing(store, inbox, keys):
    async def check(app, pilot):
        await pilot.press("l", "a", *keys)
        assert prompts(app, "todos") == ["Today", label("a"), label("b")]
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "b"]


def test_add_todo_without_lists_does_nothing(store):
    async def check(app, pilot):
        app.query_one("#todos").focus()
        await pilot.press("a")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["e", "enter"])
def test_edit_highlighted_todo(store, inbox, key):
    async def check(app, pilot):
        todos = app.query_one("#todos", OptionList)
        todos.focus()
        todos.highlighted = 2
        await pilot.press(key)
        assert app.screen.query_one("Input").value == "b"
        await type_in_prompt(pilot, "c", clear=True)
        assert prompts(app, "todos") == ["Today", label("a"), label("c")]
        assert highlighted(app) == label("c")

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "c"]


@pytest.mark.parametrize("keys", [["escape"], ["ctrl+u", "enter"]])
def test_edit_cancelled_or_blank_keeps_text(store, inbox, keys):
    async def check(app, pilot):
        await pilot.press("l")
        await pilot.press("e", *keys)
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert texts(store, inbox) == ["a", "b"]


def test_edit_without_highlighted_todo_does_nothing(store, inbox):
    async def check(app, pilot):
        app.query_one("#todos").focus()
        await pilot.press("e")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)


@pytest.mark.parametrize("key, days", [("full_stop", 1), ("comma", -1)])
def test_shift_date_of_highlighted_todo(store, inbox, key, days):
    async def check(app, pilot):
        await pilot.press("l", "j")
        await pilot.press(key, key)
        later = [human_date(date.today() + timedelta(days=2 * days)), label("b")]
        today = ["Today", label("a")]
        assert prompts(app, "todos") == (today + later if days > 0 else later + today)
        assert highlighted(app) == label("b")

    run(KansoApp(store), check)


def test_shift_date_without_highlighted_todo_does_nothing(store, inbox):
    async def check(app, pilot):
        await pilot.press("full_stop", "comma")
        assert prompts(app, "todos") == ["Today", label("a"), label("b")]

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
        app.query_one("#todos", OptionList).highlighted = 2
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
        await pilot.press("l", "a", *"hjklgG", "full_stop", "comma", "enter")

    run(KansoApp(store), check)
    assert [t.text for t in store.todos(inbox.id)] == ["a", "hjklgG.,", "b"]


def test_space_toggles_highlighted_todo(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "j", "space")
        assert highlighted(app) == label("b").replace("[ ]", "[x]")
        assert prompts(app, "todos")[1:] == [label("b").replace("[ ]", "[x]"), label("a")]
        await pilot.press("space")
        assert highlighted(app) == label("b")
        assert prompts(app, "todos")[1:] == [label("a"), label("b")]

    run(KansoApp(store), check)
    assert [(t.done, t.completed_at) for t in store.todos(inbox.id)] == [(False, None), (False, None)]


def test_toggle_marks_done_in_store(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "space")

    run(KansoApp(store), check)
    assert [(t.done, t.completed_at) for t in store.todos(inbox.id)] == [(True, date.today()), (False, None)]


@pytest.mark.parametrize("start, remaining, next_highlight", [(0, "bc", "b"), (1, "ac", "c"), (2, "ab", "b")])
def test_delete_highlights_next_or_previous(store, inbox, start, remaining, next_highlight):
    store.add_todo(inbox.id, "c")

    async def check(app, pilot):
        await pilot.press("l", *["j"] * start, "d")
        assert prompts(app, "todos") == ["Today", *(label(t) for t in remaining)]
        assert highlighted(app) == label(next_highlight)

    run(KansoApp(store), check)
    assert "".join(t.text for t in store.todos(inbox.id)) == remaining


def test_delete_last_remaining_todo(store):
    todo_list = store.add_list("Inbox")
    store.add_todo(todo_list.id, "a")

    async def check(app, pilot):
        await pilot.press("l", "d")
        assert prompts(app, "todos") == []
        assert app.query_one("#todos", OptionList).highlighted is None

    run(KansoApp(store), check)
    assert store.todos(todo_list.id) == []


@pytest.mark.parametrize("key", ["space", "d", "full_stop", "comma"])
def test_todo_actions_ignored_while_lists_focused(store, inbox, key):
    async def check(app, pilot):
        await pilot.press("l", "h", key)
        assert app.screen is app.screen_stack[0]
        assert prompts(app, "todos") == ["Today", label("a"), label("b")]

    run(KansoApp(store), check)
    assert [(t.text, t.done) for t in store.todos(inbox.id)] == [("a", False), ("b", False)]


def test_toggle_and_delete_without_highlighted_todo_do_nothing(store, inbox):
    async def check(app, pilot):
        await pilot.press("space", "d")
        assert prompts(app, "todos") == ["Today", label("a"), label("b")]

    run(KansoApp(store), check)
    assert [(t.text, t.done) for t in store.todos(inbox.id)] == [("a", False), ("b", False)]


def test_space_and_d_type_into_prompt(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "a", "d", "space", "d", "enter")

    run(KansoApp(store), check)
    assert [t.text for t in store.todos(inbox.id)] == ["a", "d d", "b"]


def list_names(store):
    return [l.name for l in store.lists()]


def highlighted_list(app):
    widget = app.query_one("#lists", OptionList)
    return str(widget.get_option_at_index(widget.highlighted).prompt)


def test_add_list_appends_and_highlights_it(store, inbox):
    store.add_list("Work")

    async def check(app, pilot):
        await pilot.press("a")
        prompt = app.screen.query_one(Input)
        assert (prompt.placeholder, prompt.max_length) == ("List", NAME_MAX)
        await type_in_prompt(pilot, " Home ")
        assert prompts(app, "lists") == ["Inbox", "Work", "Home"]
        assert highlighted_list(app) == "Home"
        assert prompts(app, "todos") == []
        assert app.focused.id == "lists"

    run(KansoApp(store), check)
    assert list_names(store) == ["Inbox", "Work", "Home"]


def test_todo_prompt_has_no_length_limit(store, inbox):
    async def check(app, pilot):
        await pilot.press("l", "a")
        prompt = app.screen.query_one(Input)
        assert (prompt.placeholder, prompt.max_length) == ("Todo", 0)

    run(KansoApp(store), check)


def test_add_first_list_then_todo(store):
    async def check(app, pilot):
        await pilot.press("a")
        await type_in_prompt(pilot, "Inbox")
        await pilot.press("enter", "a")
        await type_in_prompt(pilot, "milk")
        assert prompts(app, "todos") == ["Today", label("milk")]

    run(KansoApp(store), check)
    assert list_names(store) == ["Inbox"]
    assert texts(store, store.lists()[0]) == ["milk"]


@pytest.mark.parametrize("keys", [["escape"], ["space", "enter"], ["enter"]])
def test_add_list_cancelled_or_blank_does_nothing(store, inbox, keys):
    async def check(app, pilot):
        await pilot.press("a", *keys)
        assert app.screen is app.screen_stack[0]
        assert prompts(app, "lists") == ["Inbox"]

    run(KansoApp(store), check)
    assert list_names(store) == ["Inbox"]


def test_edit_highlighted_list(store, inbox):
    store.add_list("Work")
    store.add_todo(store.lists()[1].id, "c")

    async def check(app, pilot):
        await pilot.press("j", "e")
        assert app.screen.query_one(Input).value == "Work"
        await type_in_prompt(pilot, "Job", clear=True)
        assert prompts(app, "lists") == ["Inbox", "Job"]
        assert highlighted_list(app) == "Job"
        assert prompts(app, "todos") == ["Today", label("c")]
        assert app.focused.id == "lists"

    run(KansoApp(store), check)
    assert list_names(store) == ["Inbox", "Job"]


@pytest.mark.parametrize("keys", [["escape"], ["ctrl+u", "enter"]])
def test_edit_list_cancelled_or_blank_keeps_name(store, inbox, keys):
    async def check(app, pilot):
        await pilot.press("e", *keys)
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert list_names(store) == ["Inbox"]


def test_edit_list_without_lists_does_nothing(store):
    async def check(app, pilot):
        await pilot.press("e")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)


def test_list_keys_type_into_prompt(store):
    async def check(app, pilot):
        await pilot.press("a", *"aehjklgG", "space", "d", "enter")

    run(KansoApp(store), check)
    assert list_names(store) == ["aehjklgG d"]


def test_list_names_render_as_plain_text_after_rename(store, inbox):
    async def check(app, pilot):
        await pilot.press("e")
        await type_in_prompt(pilot, "[b]x[/b]", clear=True)
        assert app.query_one("#lists").render_line(0).text.strip() == "[b]x[/b]"

    run(KansoApp(store), check)


@pytest.fixture
def dated(store):
    """Inbox with "a" and "c" for tomorrow and "b" for today, added in that order."""
    todo_list = store.add_list("Inbox")
    tomorrow = date.today() + timedelta(days=1)
    for text, day in [("a", tomorrow), ("b", date.today()), ("c", tomorrow)]:
        store.add_todo(todo_list.id, text, scheduled_at=day)
    return todo_list


def test_todos_grouped_under_date_titles(store, dated):
    async def check(app, pilot):
        assert prompts(app, "todos") == ["Today", label("b"), "Tomorrow", label("a"), label("c")]

    run(KansoApp(store), check)


def test_cursor_skips_titles(store, dated):
    async def check(app, pilot):
        await pilot.press("l")
        assert highlighted(app) == label("b")
        await pilot.press("j")
        assert highlighted(app) == label("a")
        await pilot.press("k")
        assert highlighted(app) == label("b")
        await pilot.press("G", "g")
        assert highlighted(app) == label("b")

    run(KansoApp(store), check)


def test_title_is_not_a_todo(store, dated):
    async def check(app, pilot):
        todos = app.query_one("#todos", OptionList)
        todos.focus()
        todos.highlighted = 0
        await pilot.press("e", "space", "d")
        assert app.screen is app.screen_stack[0]
        assert prompts(app, "todos") == ["Today", label("b"), "Tomorrow", label("a"), label("c")]

    run(KansoApp(store), check)


def test_add_inserts_below_highlighted_on_its_date(store, dated):
    async def check(app, pilot):
        await pilot.press("l", "j", "a")
        await type_in_prompt(pilot, "new")
        assert prompts(app, "todos") == ["Today", label("b"), "Tomorrow", label("a"), label("new"), label("c")]
        assert highlighted(app) == label("new")

    run(KansoApp(store), check)
    assert texts(store, dated) == ["a", "new", "b", "c"]
    assert store.todos(dated.id)[1].scheduled_at == date.today() + timedelta(days=1)


def test_add_below_unscheduled_stays_unscheduled(store):
    todo_list = store.add_list("Inbox")
    store.add_todo(todo_list.id, "a", scheduled_at=None)

    async def check(app, pilot):
        await pilot.press("l", "a")
        await type_in_prompt(pilot, "b")
        assert prompts(app, "todos") == ["No date", label("a"), label("b")]

    run(KansoApp(store), check)


def test_add_without_highlight_is_for_today(store, dated):
    async def check(app, pilot):
        app.query_one("#todos").focus()
        await pilot.press("a")
        await type_in_prompt(pilot, "new")
        assert prompts(app, "todos") == ["Today", label("b"), label("new"), "Tomorrow", label("a"), label("c")]

    run(KansoApp(store), check)


@pytest.mark.parametrize("start, next_highlight", [(0, "a"), (1, "c"), (2, "a")])
def test_delete_highlights_next_or_previous_as_shown(store, dated, start, next_highlight):
    async def check(app, pilot):
        await pilot.press("l", *["j"] * start, "d")
        assert highlighted(app) == label(next_highlight)

    run(KansoApp(store), check)


def test_deleting_last_todo_of_a_date_removes_its_title(store, dated):
    async def check(app, pilot):
        await pilot.press("l", "d")
        assert prompts(app, "todos") == ["Tomorrow", label("a"), label("c")]

    run(KansoApp(store), check)


def test_todo_label_shows_labels(store):
    todo = store.add_todo(store.add_list("Inbox").id, "Buy milk", labels=["errands", "[b]x"])
    content = todo_label(todo)
    assert str(content) == "[ ] Buy milk  #errands   #[b]x "
    assert [(content.plain[s.start : s.end], s.style) for s in content.spans] == [
        (" #errands ", LABEL_STYLE),
        (" #[b]x ", LABEL_STYLE),
    ]


def suggest(labels, value):
    return asyncio.run(LabelSuggester(labels).get_suggestion(value))


@pytest.mark.parametrize(
    "value, expected",
    [
        ("w", "work "),
        ("W", "work "),
        ("h", "Home "),
        ("home x", None),
        ("work w", "work workshop "),
        ("work ", None),
        ("", None),
        ("z", None),
        ("a  wo", "a  work "),
    ],
)
def test_label_suggester(value, expected):
    assert suggest(["errands", "Home", "work", "workshop"], value) == expected


@pytest.fixture
def labelled(store):
    todo_list = store.add_list("Inbox")
    store.add_todo(todo_list.id, "a", labels=["home", "work"])
    store.add_todo(todo_list.id, "b")
    return todo_list


def with_chips(text: str, *labels: str) -> str:
    return label(text) + "".join(f"  #{l} " for l in labels)


def labels_of(store, todo_list):
    return [t.labels for t in store.todos(todo_list.id)]


def test_edit_labels_prefilled_and_saved(store, labelled):
    async def check(app, pilot):
        await pilot.press("l", "number_sign")
        prompt = app.screen.query_one(Input)
        assert (prompt.value, prompt.placeholder) == ("home work ", "Labels")
        await pilot.press(*"#urgent", "enter")
        assert highlighted(app) == with_chips("a", "home", "work", "urgent")

    run(KansoApp(store), check)
    assert labels_of(store, labelled) == [["home", "work", "urgent"], []]


@pytest.mark.parametrize("accept", ["tab", "right"])
def test_edit_labels_autocompletes_existing(store, labelled, accept):
    async def check(app, pilot):
        await pilot.press("l", "j", "number_sign", "w")
        await pilot.pause()
        await pilot.press(accept, "h")
        await pilot.pause()
        await pilot.press(accept, "enter")
        assert highlighted(app) == with_chips("b", "work", "home")

    run(KansoApp(store), check)
    assert labels_of(store, labelled) == [["home", "work"], ["work", "home"]]


def test_tab_without_suggestion_keeps_typing(store, labelled):
    async def check(app, pilot):
        await pilot.press("l", "j", "number_sign", "z", "tab", "enter")

    run(KansoApp(store), check)
    assert labels_of(store, labelled)[1] == ["z"]


def test_clearing_labels(store, labelled):
    async def check(app, pilot):
        await pilot.press("l", "number_sign", "ctrl+u", "enter")
        assert highlighted(app) == label("a")

    run(KansoApp(store), check)
    assert labels_of(store, labelled) == [[], []]


def test_edit_labels_cancelled_keeps_them(store, labelled):
    async def check(app, pilot):
        await pilot.press("l", "number_sign", "ctrl+u", "escape")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert labels_of(store, labelled)[0] == ["home", "work"]


def test_invalid_label_is_reported_and_not_saved(store, labelled):
    async def check(app, pilot):
        await pilot.press("l", "number_sign", *"x" * 51, "enter")
        assert [n.severity for n in app._notifications] == ["error"]
        assert highlighted(app) == with_chips("a", "home", "work")

    run(KansoApp(store), check)
    assert labels_of(store, labelled)[0] == ["home", "work"]


def test_edit_labels_needs_highlighted_todo(store, labelled):
    async def check(app, pilot):
        await pilot.press("number_sign")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)


def test_hash_types_into_prompt(store, labelled):
    async def check(app, pilot):
        await pilot.press("l", "j", "e", "ctrl+u", "number_sign", "1", "enter")

    run(KansoApp(store), check)
    assert texts(store, labelled) == ["a", "#1"]


def test_blank_todo_text_still_ignored(store, labelled):
    async def check(app, pilot):
        await pilot.press("l", "a", "space", "enter")

    run(KansoApp(store), check)
    assert texts(store, labelled) == ["a", "b"]


# Repeats


def test_describe_rule(store):
    inbox = store.add_list("Inbox")
    rules = [
        {"kind": "days"},
        {"kind": "days", "interval": 3},
        {"kind": "weekdays", "weekdays": [4, 0, 2]},
        {"kind": "monthdays", "monthdays": [15, 1]},
    ]
    described = [describe_rule(store.add_repeat(inbox.id, "a", **rule)) for rule in rules]
    assert described == ["Daily", "Every 3 days", "Mon, Wed, Fri", "Monthly on 1, 15"]


def test_repeat_label_is_plain_text_with_rule_chip(store):
    content = repeat_label(store.add_repeat(store.add_list("Inbox").id, "[b]Water"))
    assert str(content) == "[b]Water  Daily "
    assert [(content.plain[s.start : s.end], s.style) for s in content.spans] == [(" Daily ", RULE_STYLE)]


def test_todo_label_marks_spawned_todos(store):
    inbox = store.add_list("Inbox")
    store.add_repeat(inbox.id, "Water")
    [todo] = store.spawn_repeats()
    assert str(todo_label(todo)) == "[ ] Water ↻"


@pytest.fixture
def repeating(store):
    """Inbox with todo "a" and daily repeats "x" and "y", not spawned yet."""
    todo_list = store.add_list("Inbox")
    store.add_todo(todo_list.id, "a")
    for text in "xy":
        store.add_repeat(todo_list.id, text)
    return todo_list


def pane(app) -> str:
    return app.query_one("#panes", ContentSwitcher).current


def highlighted_repeat(app):
    widget = app.query_one("#repeats", OptionList)
    return str(widget.get_option_at_index(widget.highlighted).prompt)


def repeats_of(store, todo_list):
    return [(r.text, describe_rule(r)) for r in store.repeats(todo_list.id)]


def test_mount_spawns_todays_repeats(store, repeating):
    async def check(app, pilot):
        assert prompts(app, "todos") == ["Today", label("a"), label("x ↻"), label("y ↻")]

    run(KansoApp(store), check)


def test_r_toggles_panes_keeping_focus_side(store, repeating):
    async def check(app, pilot):
        await pilot.press("r")
        assert pane(app) == "repeats" and app.focused.id == "lists"
        assert prompts(app, "repeats") == ["x  Daily ", "y  Daily "]
        await pilot.press("l")
        assert app.focused.id == "repeats" and highlighted_repeat(app) == "x  Daily "
        await pilot.press("r")
        assert pane(app) == "todos" and app.focused.id == "todos"
        await pilot.press("r", "h")
        assert pane(app) == "repeats" and app.focused.id == "lists"

    run(KansoApp(store), check)


def test_highlighting_a_list_shows_its_repeats(store, repeating):
    other = store.add_list("Other")
    store.add_repeat(other.id, "z", kind="days", interval=2)

    async def check(app, pilot):
        await pilot.press("r", "j")
        assert prompts(app, "repeats") == ["z  Every 2 days "]

    run(KansoApp(store), check)


@pytest.mark.parametrize("key", ["space", "number_sign", "full_stop", "comma"])
def test_todo_actions_disabled_while_repeats_shown(store, repeating, key):
    async def check(app, pilot):
        await pilot.press("r", "l", key)
        assert app.screen is app.screen_stack[0]
        assert not app.check_action("toggle_todo", ())
        await pilot.press("r")
        assert app.check_action("toggle_todo", ())

    run(KansoApp(store), check)
    assert [(t.done, t.scheduled_at) for t in store.todos(repeating.id)] == [(False, date.today())] * 3


async def open_dialog(pilot, *keys: str) -> RepeatDialog:
    await pilot.press("r", "l", *keys)
    assert isinstance(pilot.app.screen, RepeatDialog)
    return pilot.app.screen


async def choose_kind(pilot, steps: int):
    """From the text field, pick the kind `steps` below the current one and move to its field."""
    await pilot.press("tab", *["down"] * steps, "enter", "tab")


def test_add_daily_repeat_spawns_todays_todo(store, repeating):
    async def check(app, pilot):
        await open_dialog(pilot, "a")
        await type_in_prompt(pilot, " Water ")
        assert app.screen is app.screen_stack[0]
        assert prompts(app, "repeats") == ["x  Daily ", "y  Daily ", "Water  Daily "]
        assert highlighted_repeat(app) == "Water  Daily " and app.focused.id == "repeats"
        assert prompts(app, "todos")[-1] == label("Water ↻")

    run(KansoApp(store), check)
    assert repeats_of(store, repeating)[-1] == ("Water", "Daily")


def test_add_every_n_days_repeat(store, repeating):
    async def check(app, pilot):
        await open_dialog(pilot, "a")
        await pilot.press(*"Water")
        await choose_kind(pilot, 1)
        assert app.focused.id == "days"
        await type_in_prompt(pilot, "3", clear=True)

    run(KansoApp(store), check)
    assert repeats_of(store, repeating)[-1] == ("Water", "Every 3 days")


def test_add_weekdays_repeat_saved_with_ctrl_s(store, repeating):
    async def check(app, pilot):
        await open_dialog(pilot, "a")
        await pilot.press(*"Gym")
        await choose_kind(pilot, 2)
        assert isinstance(app.focused, SelectionList)
        await pilot.press("space", "down", "down", "space", "ctrl+s")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert repeats_of(store, repeating)[-1] == ("Gym", "Mon, Wed")


def test_add_monthdays_repeat(store, repeating):
    async def check(app, pilot):
        await open_dialog(pilot, "a")
        await pilot.press(*"Rent")
        await choose_kind(pilot, 3)
        await type_in_prompt(pilot, "15 x1")

    run(KansoApp(store), check)
    assert repeats_of(store, repeating)[-1] == ("Rent", "Monthly on 1, 15")


def test_dialog_shows_only_the_chosen_kinds_field(store, repeating):
    async def check(app, pilot):
        dialog = await open_dialog(pilot, "a")
        assert dialog.fields.current is None
        for field in ["days", "weekdays", "monthdays", None]:
            await pilot.press("tab", "down", "enter", "shift+tab")
            assert dialog.fields.current == field

    run(KansoApp(store), check)


@pytest.mark.parametrize(
    "steps, value, error",
    [(1, "0", "Interval"), (2, None, "weekdays"), (3, "", "days of month"), (3, "32", "1-31")],
)
@pytest.mark.parametrize("save", ["ctrl+s", "escape"])
def test_invalid_repeat_is_reported_and_dialog_stays(store, repeating, steps, value, error, save):
    async def check(app, pilot):
        dialog = await open_dialog(pilot, "a")
        await pilot.press(*"Water")
        await choose_kind(pilot, steps)
        if value is not None:
            await pilot.press("ctrl+u", *value)
        await pilot.press(save)
        assert app.screen is dialog
        [notification] = app._notifications
        assert notification.severity == "error" and error in notification.message

    run(KansoApp(store), check)
    assert len(store.repeats(repeating.id)) == 2


def test_escape_saves_new_repeat(store, repeating):
    async def check(app, pilot):
        await open_dialog(pilot, "a")
        await pilot.press(*"Water", "tab", "down", "escape")
        assert app.screen is app.screen_stack[0] and app.focused.id == "repeats"

    run(KansoApp(store), check)
    assert repeats_of(store, repeating)[-1] == ("Water", "Daily")


@pytest.mark.parametrize("keys", [["escape"], [" ", "enter"], ["ctrl+s"]])
def test_new_repeat_with_blank_text_is_dropped(store, repeating, keys):
    async def check(app, pilot):
        await open_dialog(pilot, "a")
        await pilot.press(*keys)
        assert app.screen is app.screen_stack[0] and app.focused.id == "repeats"

    run(KansoApp(store), check)
    assert len(store.repeats(repeating.id)) == 2


@pytest.mark.parametrize("save", ["escape", "enter"])
def test_editing_repeat_to_blank_text_is_reported(store, repeating, save):
    async def check(app, pilot):
        dialog = await open_dialog(pilot, "e")
        await pilot.press("ctrl+u", save)
        assert app.screen is dialog
        assert ["blank" in n.message for n in app._notifications] == [True]

    run(KansoApp(store), check)
    assert repeats_of(store, repeating)[0] == ("x", "Daily")


def test_escape_without_changes_writes_nothing(store, repeating):
    stamp = store.repeats(repeating.id)[0].modified_at

    async def check(app, pilot):
        await open_dialog(pilot, "e")
        await pilot.press("tab", "down", "enter", "up", "enter", "escape")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert store.repeats(repeating.id)[0].modified_at == stamp


def test_escape_saves_edited_repeat(store, repeating):
    async def check(app, pilot):
        await open_dialog(pilot, "e")
        await pilot.press("end", "z", "escape")
        assert highlighted_repeat(app) == "xz  Daily "

    run(KansoApp(store), check)
    assert repeats_of(store, repeating)[0] == ("xz", "Daily")


def test_kinds_cursor_starts_on_the_chosen_kind(store, repeating):
    store.update_repeat(store.repeats(repeating.id)[0].id, kind="monthdays", monthdays=[1])

    async def check(app, pilot):
        dialog = await open_dialog(pilot, "e")
        await pilot.press("tab", "up", "enter")
        assert dialog.kind() == "weekdays"

    run(KansoApp(store), check)


def test_vim_movement_in_dialog(store, repeating):
    async def check(app, pilot):
        dialog = await open_dialog(pilot, "a")
        await pilot.press(*"Gym", "tab", "j", "j", "k", "j", "enter")
        assert dialog.kind() == "weekdays"
        await pilot.press("tab", "G", "space", "g", "space", "j", "j", "space", "j", "k", "space", "space")
        assert sorted(dialog.weekdays.selected) == [0, 2, 6]
        await pilot.press("ctrl+s")

    run(KansoApp(store), check)
    assert repeats_of(store, repeating)[-1] == ("Gym", "Mon, Wed, Sun")


def test_app_keys_ignored_inside_dialog(store, repeating):
    async def check(app, pilot):
        dialog = await open_dialog(pilot, "a")
        await pilot.press("tab", "a", "e", "d", "r", "h", "l")
        assert app.screen is dialog and len(app.screen_stack) == 2
        assert pane(app) == "repeats"

    run(KansoApp(store), check)
    assert len(store.repeats(repeating.id)) == 2


@pytest.mark.parametrize("key", ["e", "enter"])
def test_edit_repeat_prefilled_and_saved(store, repeating, key):
    store.update_repeat(store.repeats(repeating.id)[1].id, kind="weekdays", weekdays=[1, 3])

    async def check(app, pilot):
        dialog = await open_dialog(pilot, "j", key)
        assert dialog.text.value == "y" and dialog.kind() == "weekdays"
        assert dialog.fields.current == "weekdays" and dialog.weekdays.selected == [1, 3]
        await pilot.press("tab", "tab", "down", "down", "down", "space", "shift+tab", "shift+tab")
        await type_in_prompt(pilot, "z", clear=True)
        assert highlighted_repeat(app) == "z  Tue "

    run(KansoApp(store), check)
    assert repeats_of(store, repeating) == [("x", "Daily"), ("z", "Tue")]


def test_edit_prefills_every_n_and_monthdays(store, repeating):
    x, y = store.repeats(repeating.id)
    store.update_repeat(x.id, interval=4)
    store.update_repeat(y.id, kind="monthdays", monthdays=[2, 20])

    async def check(app, pilot):
        dialog = await open_dialog(pilot, "e")
        assert (dialog.kind(), dialog.fields.current, dialog.interval.value) == ("days", "days", "4")
        await pilot.press("escape", "j", "e")
        dialog = app.screen
        assert (dialog.kind(), dialog.monthdays.value) == ("monthdays", "2 20")

    run(KansoApp(store), check)


def test_new_repeat_dialog_defaults(store, repeating):
    async def check(app, pilot):
        dialog = await open_dialog(pilot, "a")
        assert (dialog.text.value, dialog.kind(), dialog.interval.value) == ("", "daily", "2")
        assert dialog.weekdays.selected == [] and dialog.monthdays.value == ""

    run(KansoApp(store), check)


def test_edit_repeat_rule_spawns_if_now_due(store, repeating):
    tomorrow = (date.today() + timedelta(days=1)).weekday()
    x = store.repeats(repeating.id)[0]
    store.update_repeat(x.id, kind="weekdays", weekdays=[tomorrow])

    async def check(app, pilot):
        assert prompts(app, "todos") == ["Today", label("a"), label("y ↻")]
        await open_dialog(pilot, "e")
        await pilot.press("tab", "up", "up", "enter", "ctrl+s")
        assert prompts(app, "todos") == ["Today", label("a"), label("y ↻"), label("x ↻")]

    run(KansoApp(store), check)


def test_edit_and_delete_need_highlighted_repeat(store, repeating):
    async def check(app, pilot):
        await pilot.press("r")
        app.query_one("#repeats").focus()
        await pilot.press("e", "d")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)
    assert len(store.repeats(repeating.id)) == 2


@pytest.mark.parametrize("start, next_highlight", [(0, "y"), (1, "x")])
def test_delete_repeat_keeps_its_todos(store, repeating, start, next_highlight):
    async def check(app, pilot):
        await pilot.press("r", "l", *["j"] * start, "d")
        assert prompts(app, "repeats") == [f"{next_highlight}  Daily "]
        assert highlighted_repeat(app) == f"{next_highlight}  Daily "

    run(KansoApp(store), check)
    assert texts(store, repeating) == ["a", "x", "y"]


def test_delete_last_repeat(store):
    todo_list = store.add_list("Inbox")
    store.add_repeat(todo_list.id, "x")

    async def check(app, pilot):
        await pilot.press("r", "l", "d")
        assert prompts(app, "repeats") == []

    run(KansoApp(store), check)


def test_add_repeat_without_lists_does_nothing(store):
    async def check(app, pilot):
        await pilot.press("r", "l", "a")
        assert app.screen is app.screen_stack[0]

    run(KansoApp(store), check)


def tomorrow_date(monkeypatch):
    tomorrow = date.today() + timedelta(days=1)

    class Tomorrow(date):
        @classmethod
        def today(cls):
            return tomorrow

    monkeypatch.setattr(app_module, "date", Tomorrow)
    return tomorrow


def test_day_change_spawns_and_redraws_keeping_highlight(store, repeating, monkeypatch):
    async def check(app, pilot):
        await pilot.press("l", "j")
        app.check_day()
        assert prompts(app, "todos") == ["Today", label("a"), label("x ↻"), label("y ↻")]
        tomorrow_date(monkeypatch)
        app.check_day()
        assert prompts(app, "todos") == ["Yesterday", label("a"), label("x ↻"), label("y ↻"), "Today", label("x ↻"), label("y ↻")]
        assert app.query_one("#todos", OptionList).highlighted == 2

    run(KansoApp(store), check)
    assert len(store.todos(repeating.id)) == 5


def test_day_change_without_lists(store, monkeypatch):
    async def check(app, pilot):
        tomorrow_date(monkeypatch)
        app.check_day()
        assert prompts(app, "todos") == []

    run(KansoApp(store), check)
