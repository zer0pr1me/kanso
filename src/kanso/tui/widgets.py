"""Widgets with vim keys, and label completion."""

from textual.binding import Binding
from textual.suggester import Suggester
from textual.widgets import OptionList, RadioSet, SelectionList

VIM_BINDINGS = [
    Binding("j", "cursor_down", show=False),
    Binding("k", "cursor_up", show=False),
    Binding("g", "first", show=False),
    Binding("G", "last", show=False),
]


class VimOptionList(OptionList):
    BINDINGS = VIM_BINDINGS


class VimSelectionList(SelectionList):
    BINDINGS = VIM_BINDINGS


class VimRadioSet(RadioSet):
    BINDINGS = [Binding("j", "next_button", show=False), Binding("k", "previous_button", show=False)]

    def on_mount(self):
        """Start the cursor on the pressed button instead of the first, once `RadioSet` has set it."""
        self.call_later(self.move_to_pressed)

    def move_to_pressed(self):
        for _ in range(self.pressed_index):
            self.action_next_button()


class LabelSuggester(Suggester):
    """Completes the word being typed to a label not entered yet, case-insensitively, then a space."""

    def __init__(self, labels: list[str]):
        super().__init__(use_cache=False)
        self.labels = labels

    async def get_suggestion(self, value: str) -> str | None:
        words = value.split(" ")
        if not words[-1]:
            return None
        typed = words[-1].casefold()
        match = next((l for l in self.labels if l.casefold().startswith(typed) and l not in words), None)
        return match and f"{value[: -len(words[-1])]}{match} "
