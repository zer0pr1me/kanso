"""Modal dialogs that save as they close."""

import calendar
from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Input, RadioButton, RadioSet

from kanso.core.models import Repeat
from kanso.tui.widgets import VimRadioSet, VimSelectionList


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

    def __init__(self, save: Callable[[dict], object], repeat: Repeat | None = None):
        super().__init__(save)
        r = repeat or Repeat(text="", position="")
        self.kind = kind = "daily" if r.kind == "days" and r.interval == 1 else r.kind
        self.text = Input(r.text, placeholder="Repeating todo")
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
