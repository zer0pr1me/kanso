"""Widgets with vim keys, key bindings that work on the Ukrainian layout too, and label completion."""

import unicodedata

from textual.binding import Binding, BindingType
from textual.suggester import Suggester
from textual.widgets import OptionList, RadioSet, SelectionList


def key_name(character: str) -> str:
    """The name Textual gives the key typing `character`: itself if alphanumeric, else its Unicode name."""
    if character.isalnum():
        return character
    return unicodedata.name(character).lower().replace(" ", "_").replace("-", "_")


US_KEYS = "qwertyuiop[]asdfghjkl;'zxcvbnm,./" + 'QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>?' + "#"
UA_KEYS = "йцукенгшщзхїфівапролджєячсмитьбю." + "ЙЦУКЕНГШЩЗХЇФІВАПРОЛДЖЄЯЧСМИТЬБЮ," + "№"
UA_LAYOUT = {key_name(us): key_name(ua) for us, ua in zip(US_KEYS, UA_KEYS, strict=True)}
"""Key names on the US layout, and what the Ukrainian (ЙЦУКЕН) layout types on the same physical key."""


def with_layouts(bindings: list[BindingType]) -> list[Binding]:
    """`bindings`, plus a copy of each on the keys the Ukrainian layout types in their places,
    hidden from the footer. Terminals send characters, not physical keys, so without the copy
    `a` does nothing while the layout types `ф`."""
    result = []
    for binding in bindings:
        binding = binding if isinstance(binding, Binding) else Binding(*binding)
        result.append(binding)
        keys = [key.strip() for key in binding.key.split(",")]
        if aliases := [UA_LAYOUT[key] for key in keys if key in UA_LAYOUT]:
            alias = Binding(",".join(aliases), binding.action, binding.description, show=False, priority=binding.priority)
            result.append(alias)
    return result


VIM_BINDINGS = with_layouts(
    [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g", "first", show=False),
        Binding("G", "last", show=False),
    ]
)


class VimOptionList(OptionList):
    BINDINGS = VIM_BINDINGS


class VimSelectionList(SelectionList):
    BINDINGS = VIM_BINDINGS


class VimRadioSet(RadioSet):
    BINDINGS = with_layouts([Binding("j", "next_button", show=False), Binding("k", "previous_button", show=False)])

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
