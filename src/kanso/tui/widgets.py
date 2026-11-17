"""Widgets with vim keys, key bindings that work on the Ukrainian layout too, and label completion."""

import unicodedata

from textual.binding import Binding, BindingType
from textual.content import Content
from textual.strip import Strip
from textual.suggester import Suggester
from textual.widgets import OptionList, RadioSet, SelectionList
from textual.widgets.option_list import Option


def key_name(character: str) -> str:
    """The name Textual gives the key typing `character`: itself if alphanumeric, else its Unicode name."""
    if character.isalnum():
        return character
    return unicodedata.name(character).lower().replace(" ", "_").replace("-", "_")


US_KEYS = "qwertyuiop[]asdfghjkl;'zxcvbnm,./" + 'QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>?' + "#"
UA_KEYS = "йцукенгшщзхїфівапролджєячсмитьбю." + "ЙЦУКЕНГШЩЗХЇФІВАПРОЛДЖЄЯЧСМИТЬБЮ," + "№"
UA_LAYOUT = {key_name(us): key_name(ua) for us, ua in zip(US_KEYS, UA_KEYS, strict=True)}
"""Key names on the US layout, and what the Ukrainian (ЙЦУКЕН) layout types on the same physical key."""


def layout_alias(binding: Binding) -> str:
    """The keys of `binding` the Ukrainian layout types in their places, comma-separated, if any."""
    keys = [key.strip() for key in binding.key.split(",")]
    return ",".join(UA_LAYOUT[key] for key in keys if key in UA_LAYOUT)


def with_layouts(bindings: list[BindingType]) -> list[Binding]:
    """`bindings`, plus a copy of each on the keys the Ukrainian layout types in their places,
    hidden from the footer. Terminals send characters, not physical keys, so without the copy
    `a` does nothing while the layout types `ф`."""
    result = []
    for binding in bindings:
        binding = binding if isinstance(binding, Binding) else Binding(*binding)
        result.append(binding)
        if aliases := layout_alias(binding):
            alias = Binding(aliases, binding.action, binding.description, show=False, priority=binding.priority)
            result.append(alias)
    return result


VIM_BINDINGS = with_layouts(
    [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "first", "First", show=False),
        Binding("G", "last", "Last", show=False),
    ]
)


class VimOptionList(OptionList):
    BINDINGS = VIM_BINDINGS


class SectionTitle(Option):
    """The title of the options after it, up to the next title; disabled, so the cursor skips it."""

    def __init__(self, title: str):
        super().__init__(Content(title), disabled=True)


class SectionedOptionList(VimOptionList):
    """Options in sections under `SectionTitle`s. Once a section's title scrolls out of view,
    it floats at the top over the section's rest, so the highlight's section always shows."""

    def floating(self) -> int | None:
        """The index of the title floating at the top, if any."""
        lines = self._lines[self.scroll_offset.y : self.scroll_offset.y + 2]
        if not lines or any(isinstance(self.options[index], SectionTitle) for index, _ in lines):
            return None  # its own title shows, or the next one would right under it
        top = lines[0][0]
        return next((i for i in range(top - 1, -1, -1) if isinstance(self.options[i], SectionTitle)), None)

    def render_line(self, y: int) -> Strip:
        if y == 0 and (title := self.floating()) is not None:
            style = self.get_visual_style("option-list--option", "option-list--option-disabled")
            return self._get_option_render(self.options[title], style)[0]
        return super().render_line(y)

    def scroll_to_highlight(self, top: bool = False):
        """Also keep a line above the highlight in view: its section's title, or another of
        its section's options with the title floating over it."""
        super().scroll_to_highlight(top)
        line = self._index_to_line.get(self.highlighted) if self.highlighted is not None else None
        if line is not None and line == self.scroll_offset.y > 0:
            self.scroll_to(y=line - 1, animate=False, immediate=True)


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
