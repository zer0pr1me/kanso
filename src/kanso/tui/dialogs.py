"""Modal dialogs that save as they close."""

import calendar
import os
import shutil
import subprocess
import sys
from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Input, RadioButton, RadioSet, Static

from kanso.core.models import Repeat, Scheduled
from kanso.node import Node
from kanso.sync.protocol import CODE_TTL, new_code
from kanso.sync.transport import SyncError
from kanso.tui.widgets import VimRadioSet, VimSelectionList, with_layouts


class Dialog[T](ModalScreen[None]):
    """Enter or escape saves the value with `save` and closes; an unchanged value (so a blank
    new one) closes without saving. If `save` raises `ValueError`, shows it and stays open."""

    BINDINGS = [Binding("escape", "save", "Save")]

    def __init__(self, save: Callable[[T], object]):
        super().__init__()
        self.save = save

    def value(self) -> T:
        raise NotImplementedError

    def on_mount(self):
        self.initial = self.value()

    def action_save(self):
        if (value := self.value()) != self.initial:
            try:
                self.save(value)
            except ValueError as error:
                self.notify(str(error), severity="error")
                return
        self.dismiss()

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self.action_save()


class TextPrompt(Dialog[str]):
    """Asks for a line of text, saved stripped."""

    BINDINGS = [Binding("tab", "complete", show=False)]
    CSS = """
    TextPrompt { align: center middle; }
    TextPrompt Input { width: 60; }
    """

    def __init__(self, save: Callable[[str], object], value: str = "", **options):
        """`options` go to the `Input`; the placeholder defaults to "Todo"."""
        super().__init__(save)
        self.input = Input(value, **{"placeholder": "Todo", **options})

    def compose(self) -> ComposeResult:
        yield self.input

    def value(self) -> str:
        return self.input.value.strip()

    def action_complete(self):
        """Accept the suggestion, if any (right arrow does too)."""
        self.input.action_cursor_right()


REPEAT_KINDS = {"daily": "Daily", "days": "Every N days", "weekdays": "Weekdays", "monthdays": "Days of month"}


class RepeatDialog(Dialog[dict]):
    """Asks for a repeat's text and rule, showing only the chosen kind's field; saves them
    as `Store.update_repeat` changes. Ctrl+s saves too, as enter only does in text fields."""

    BINDINGS = [Binding("ctrl+s", "save", "Save")]
    CSS = """
    RepeatDialog { align: center middle; }
    RepeatDialog > Vertical { width: 60; height: auto; border: round $accent; background: $surface; }
    RepeatDialog ContentSwitcher, RepeatDialog SelectionList { height: auto; }
    """

    def __init__(self, save: Callable[[dict], object], template: Scheduled | None = None, placeholder: str = "Repeating todo"):
        """Edits `template`, a repeat or habit, else asks for a new one."""
        super().__init__(save)
        r = template or Repeat(text="", position="")
        self.kind = kind = "daily" if r.kind == "days" and r.interval == 1 else r.kind
        self.text = Input(r.text, placeholder=placeholder)
        self.kinds = VimRadioSet(*(RadioButton(label, kind == k, id=f"kind-{k}") for k, label in REPEAT_KINDS.items()))
        self.interval = Input(str(max(r.interval, 2)), placeholder="Days", restrict=r"[0-9]*", id="days")
        days = enumerate(calendar.day_abbr)
        self.weekdays = VimSelectionList(*((name, d, d in r.weekdays) for d, name in days), id="weekdays")
        monthdays = " ".join(map(str, r.monthdays))
        self.monthdays = Input(monthdays, placeholder="Days of month, e.g. 1 15", restrict=r"[0-9 ]*", id="monthdays")
        self.fields = ContentSwitcher(self.interval, self.weekdays, self.monthdays, initial=self.field_for(kind))

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self.text
            yield self.kinds
            yield self.fields

    @staticmethod
    def field_for(kind: str) -> str | None:
        return None if kind == "daily" else kind

    def value(self) -> dict:
        text = self.text.value.strip()
        match self.kind:
            case "daily":
                return {"text": text, "kind": "days", "interval": 1}
            case "days":
                return {"text": text, "kind": "days", "interval": int(self.interval.value or 0)}
            case "weekdays":
                return {"text": text, "kind": "weekdays", "weekdays": sorted(self.weekdays.selected)}
            case _:
                return {"text": text, "kind": "monthdays", "monthdays": [int(d) for d in self.monthdays.value.split()]}

    def on_radio_set_changed(self, event: RadioSet.Changed):
        self.kind = event.pressed.id.removeprefix("kind-")
        self.fields.current = self.field_for(self.kind)


class ConfirmDialog(ModalScreen[bool]):
    """Asks `question`; dismisses with whether it was confirmed."""

    BINDINGS = with_layouts([Binding("y,enter", "answer(True)", "Yes"), Binding("n,escape", "answer(False)", "No")])
    CSS = """
    ConfirmDialog { align: center middle; }
    ConfirmDialog > Vertical { width: 60; height: auto; border: round $error; background: $surface; padding: 0 1; }
    ConfirmDialog .keys { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Content(self.question), id="question")
            yield Static("y or enter: yes · n or escape: no", classes="keys")

    def action_answer(self, yes: bool):
        self.dismiss(yes)


def clipboard_command() -> list[str] | None:
    """The system's clipboard tool, if installed and usable here."""
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy"]
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard"]
    if sys.platform == "darwin":
        return ["pbcopy"]
    if sys.platform == "win32":
        return ["clip"]
    return None


def copy(app: App, text: str) -> bool:
    """Copy `text` with the system's clipboard tool, and through the terminal (OSC 52), which
    reaches the local clipboard over SSH but which tmux and some terminals ignore. Whether the
    system tool copied it."""
    app.copy_to_clipboard(text)
    if not (command := clipboard_command()):
        return False
    try:
        subprocess.run(command, input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


class ConnectDialog(ModalScreen[None]):
    """Shows this device's ID and a fresh pairing code for another device to connect with,
    and connects to another device by its ID and code, joining the two networks.

    Without sync (the node's `sync_note` says why), only shows the ID and code.
    """

    BINDINGS = [("escape", "dismiss", "Close"), Binding("ctrl+y", "copy", "Copy ID and code")]
    CSS = """
    ConnectDialog { align: center middle; }
    ConnectDialog > Vertical { width: 72; height: auto; border: round $accent; background: $surface; padding: 0 1; }
    ConnectDialog .title { text-style: bold; color: $text-accent; margin-top: 1; }
    ConnectDialog #status { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, node: Node):
        super().__init__()
        self.key, self.node = node.key, node
        self.code = new_code(node.store)
        self.peer = Input(placeholder="Other device's ID", id="peer")
        self.peer_code = Input(placeholder="Its code", id="code")
        self.status = Static(node.sync_note or "", id="status")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("This device", classes="title")
            yield Static(self.key, id="key")
            yield Static(f"Code {self.code}, valid {CODE_TTL // 60} minutes (ctrl+y copies both)", id="own-code")
            if self.node.sync:
                yield Static("Connect to", classes="title")
                yield self.peer
                yield self.peer_code
            yield self.status

    def action_copy(self):
        if copy(self.app, f"{self.key} {self.code}"):
            self.notify("Copied this device's ID and code")
        else:
            self.notify("Sent the ID and code to the terminal's clipboard; if they didn't arrive, it doesn't support that")

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        key, code = self.peer.value.strip(), self.peer_code.value.strip()
        if not (key and code):
            (self.peer_code if key else self.peer).focus()
        elif key == self.key:
            self.notify("That's this device's own ID", severity="error")
        else:
            self.status.update("Connecting…")
            self.run_worker(self.connect(key, code), exclusive=True)

    async def connect(self, key: str, code: str):
        try:
            result = await self.node.pair(key, code)
        except SyncError as error:
            self.status.update("")
            self.notify(str(error), severity="error")
            return
        self.notify(f"Connected to {result.peer}")
        self.dismiss()
