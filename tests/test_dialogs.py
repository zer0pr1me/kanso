import pytest
from textual.app import App
from textual.widgets import Input

from conftest import notifications, run
from kanso.core.models import Repeat
from kanso.core.repeats import clean_rule
from kanso.tui import dialogs
from kanso.tui.dialogs import ConfirmDialog, RepeatDialog, TextPrompt
from kanso.tui.widgets import LabelSuggester


class Host(App):
    """Opens the dialog `make(save)` on start; `save` records values, refusing "bad"."""

    def __init__(self, make):
        super().__init__()
        self.make = make
        self.saved = []

    def save(self, value):
        if value == "bad":
            raise ValueError("Bad value")
        self.saved.append(value)

    def on_mount(self):
        self.dialog = self.make(self.save)
        self.push_screen(self.dialog)

    def is_open(self) -> bool:
        return self.screen is self.dialog


def prompt(value: str = "", **options) -> Host:
    return Host(lambda save: TextPrompt(save, value, **options))


# Text prompt


@pytest.mark.parametrize("key", ["enter", "escape"])
def test_enter_or_escape_saves_stripped_and_closes(key):
    async def check(app, pilot):
        await pilot.press("end", *" b ", key)
        assert not app.is_open()
        assert app.saved == ["a b"]

    run(prompt("a"), check)


@pytest.mark.parametrize(
    "value, keys",
    [("a", ["enter"]), ("a", ["end", "space", "escape"]), ("", ["escape"]), ("", ["space", "enter"])],
)
def test_unchanged_or_blank_new_value_closes_without_saving(value, keys):
    async def check(app, pilot):
        await pilot.press(*keys)
        assert not app.is_open()
        assert app.saved == []

    run(prompt(value), check)


def test_refused_value_is_reported_and_prompt_stays():
    async def check(app, pilot):
        await pilot.press(*"bad", "enter")
        assert app.is_open()
        assert notifications(app) == ["Bad value"]
        await pilot.press("ctrl+u", "x", "escape")
        assert app.saved == ["x"] and not app.is_open()

    run(prompt(), check)


def test_options_go_to_the_input():
    async def check(app, pilot):
        assert app.dialog.query_one(Input).placeholder == "Todo"

    run(prompt(), check)

    async def check_options(app, pilot):
        field = app.dialog.query_one(Input)
        assert (field.placeholder, field.max_length) == ("List", 5)

    run(prompt(placeholder="List", max_length=5), check_options)


@pytest.mark.parametrize("keys, expected", [(["w", "tab"], "work "), (["z", "tab"], "z")])
def test_tab_accepts_suggestion_if_any(keys, expected):
    async def check(app, pilot):
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        assert app.dialog.query_one(Input).value == expected

    run(prompt(suggester=LabelSuggester(["work"])), check)


# Repeat dialog


def repeat_dialog(**rule) -> Host:
    repeat = Repeat(text="x", position="V", **clean_rule(**rule)) if rule else None
    return Host(lambda save: RepeatDialog(save, repeat))


def test_new_repeat_defaults_to_daily():
    async def check(app, pilot):
        dialog = app.dialog
        assert dialog.value() == {"text": "", "kind": "days", "interval": 1}
        assert (dialog.kind, dialog.fields.current, dialog.interval.value) == ("daily", None, "2")
        assert dialog.weekdays.selected == [] and dialog.monthdays.value == ""

    run(repeat_dialog(), check)


@pytest.mark.parametrize(
    "steps, keys, rule",
    [
        (0, [], {"kind": "days", "interval": 1}),
        (1, ["ctrl+u", "3"], {"kind": "days", "interval": 3}),
        (2, ["space", "down", "down", "space"], {"kind": "weekdays", "weekdays": [0, 2]}),
        (3, [*"15 1"], {"kind": "monthdays", "monthdays": [15, 1]}),
    ],
)
@pytest.mark.parametrize("save", ["ctrl+s", "escape"])
def test_each_kind_shows_its_field_and_saves(steps, keys, rule, save):
    async def check(app, pilot):
        await pilot.press(*"Water", "tab", *["down"] * steps, "enter", "tab")
        assert app.dialog.fields.current == RepeatDialog.field_for(app.dialog.kind)
        await pilot.press(*keys, save)
        assert not app.is_open()

    app = repeat_dialog()
    run(app, check)
    assert app.saved == [{"text": "Water", **rule}]


def test_enter_in_text_saves():
    async def check(app, pilot):
        await pilot.press(*"Water", "enter")

    app = repeat_dialog()
    run(app, check)
    assert app.saved == [{"text": "Water", "kind": "days", "interval": 1}]


@pytest.mark.parametrize(
    "rule, kind, shown",
    [
        ({"kind": "days"}, "daily", None),
        ({"kind": "days", "interval": 4}, "days", "4"),
        ({"kind": "weekdays", "weekdays": [1, 3]}, "weekdays", [1, 3]),
        ({"kind": "monthdays", "monthdays": [2, 20]}, "monthdays", "2 20"),
    ],
)
def test_edit_prefills_and_unchanged_closes_without_saving(rule, kind, shown):
    async def check(app, pilot):
        dialog = app.dialog
        assert (dialog.text.value, dialog.kind, dialog.fields.current) == ("x", kind, RepeatDialog.field_for(kind))
        field = {"days": dialog.interval.value, "weekdays": dialog.weekdays.selected, "monthdays": dialog.monthdays.value}
        assert field.get(kind) == shown
        await pilot.press("escape")
        assert not app.is_open()

    app = repeat_dialog(**rule)
    run(app, check)
    assert app.saved == []


def test_kinds_cursor_starts_on_the_chosen_kind():
    async def check(app, pilot):
        await pilot.press("tab", "up", "enter")
        assert app.dialog.kind == "weekdays"

    run(repeat_dialog(kind="monthdays", monthdays=[1]), check)


def test_vim_keys_move_through_kinds_and_weekdays():
    async def check(app, pilot):
        await pilot.press("tab", "j", "j", "k", "j", "enter")
        assert app.dialog.kind == "weekdays"
        await pilot.press("tab", "G", "space", "g", "space", "j", "j", "space")
        assert sorted(app.dialog.weekdays.selected) == [0, 2, 6]

    run(repeat_dialog(), check)


# Clipboard


@pytest.mark.parametrize(
    "env, installed, platform, expected",
    [
        ({"WAYLAND_DISPLAY": "w", "DISPLAY": ":0"}, {"wl-copy", "xclip"}, "linux", ["wl-copy"]),
        ({"WAYLAND_DISPLAY": "w", "DISPLAY": ":0"}, {"xclip"}, "linux", ["xclip", "-selection", "clipboard"]),
        ({"DISPLAY": ":0"}, {"wl-copy", "xclip"}, "linux", ["xclip", "-selection", "clipboard"]),
        ({}, {"wl-copy", "xclip"}, "linux", None),
        ({}, set(), "darwin", ["pbcopy"]),
        ({}, set(), "win32", ["clip"]),
    ],
)
def test_clipboard_command_fits_the_session(monkeypatch, env, installed, platform, expected):
    for var in ("WAYLAND_DISPLAY", "DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    monkeypatch.setattr(dialogs.shutil, "which", lambda name: name in installed and f"/bin/{name}")
    monkeypatch.setattr(dialogs.sys, "platform", platform)
    assert dialogs.clipboard_command() == expected


class Recorder:
    clipboard = None

    def copy_to_clipboard(self, text):
        self.clipboard = text


@pytest.mark.parametrize(
    "command, copied",
    [(None, False), (["cat"], True), (["false"], False), (["no-such-tool-anywhere"], False)],
)
def test_copy_uses_the_tool_and_the_terminal(monkeypatch, command, copied):
    monkeypatch.setattr(dialogs, "clipboard_command", lambda: command)
    app = Recorder()
    assert dialogs.copy(app, "key code") is copied
    assert app.clipboard == "key code"


def test_copy_passes_the_text_to_the_tool(monkeypatch):
    calls = []
    monkeypatch.setattr(dialogs, "clipboard_command", lambda: ["tool"])
    monkeypatch.setattr(dialogs.subprocess, "run", lambda command, **options: calls.append((command, options["input"])))
    assert dialogs.copy(Recorder(), "key code")
    assert calls == [(["tool"], b"key code")]


# Confirmation


@pytest.mark.parametrize("keys, answer", [(["y"], True), (["enter"], True), (["n"], False), (["escape"], False), (["x", "n"], False)])
def test_confirm_dialog_answers(keys, answer):
    answers = []

    class Asker(App):
        def on_mount(self):
            self.push_screen(ConfirmDialog('Delete "[b]x"?'), answers.append)

    async def check(app, pilot):
        assert str(app.screen.query_one("#question").render()) == 'Delete "[b]x"?'
        await pilot.press(*keys)

    run(Asker(), check)
    assert answers == [answer]
