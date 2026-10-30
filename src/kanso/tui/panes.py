"""The app's three panes: lists, and a list's todos or repeats.

Each pane binds its own keys, so they only act while it's focused and the footer shows
just those. Panes save through dialogs and redraw themselves from the store.
"""

from collections.abc import Callable, Iterator
from uuid import UUID

from textual.binding import Binding
from textual.content import Content
from textual.widgets.option_list import Option

from kanso.core.models import Record, Repeat, Todo, TodoList
from kanso.core.order import sections
from kanso.core.store import NAME_MAX, Store
from kanso.tui.dialogs import RepeatDialog, TextPrompt
from kanso.tui.format import repeat_label, section_title, todo_label
from kanso.tui.widgets import LabelSuggester, VimOptionList


def successor[T: Record](records: list[T], record: T) -> T | None:
    """What takes `record`'s place once it's removed: the next one, else the previous one."""
    rest = [r for r in records if r.id != record.id]
    index = next(i for i, r in enumerate(records) if r.id == record.id)
    return rest[min(index, len(rest) - 1)] if rest else None


class RecordPane[T: Record](VimOptionList):
    """Records from the store, one option each, with the option id being the record id."""

    BINDINGS = [("r", "app.toggle_repeats", "Todos/Repeats")]

    def __init__(self, store: Store, **options):
        super().__init__(**options)
        self.store = store
        self.records: list[T] = []

    def load(self) -> list[T]:
        raise NotImplementedError

    def label(self, record: T) -> Content:
        raise NotImplementedError

    def make_options(self) -> Iterator[Option]:
        return (Option(self.label(r), str(r.id)) for r in self.records)

    def shown(self) -> list[T]:
        """The records in the order shown."""
        return self.records

    def show(self, highlight: T | None = None):
        """Reload, highlighting `highlight` if given."""
        self.records = self.load()
        self.clear_options()
        self.add_options(self.make_options())
        if highlight:
            self.highlighted = self.get_option_index(str(highlight.id))

    def show_without(self, record: T):
        """Reload after `record` was deleted, highlighting the one now in its place."""
        self.show(successor(self.shown(), record))

    @property
    def current(self) -> T | None:
        """The highlighted record."""
        option = self.highlighted_option
        return next((r for r in self.records if option and str(r.id) == option.id), None)


class ListsPane(RecordPane[TodoList]):
    BINDINGS = [("a", "add", "Add"), ("e", "edit", "Edit"), Binding("right,l", "app.focus_pane", show=False)]

    def load(self) -> list[TodoList]:
        return self.store.lists()

    def label(self, todo_list: TodoList) -> Content:
        return Content(todo_list.name)

    def show(self, highlight: TodoList | None = None):
        """Reload, highlighting `highlight`, else the first list."""
        super().show(highlight)
        if self.records and not highlight:
            self.highlighted = 0

    def prompt(self, save: Callable[[str], object], name: str = ""):
        self.app.push_screen(TextPrompt(save, name, placeholder="List", max_length=NAME_MAX))

    def action_add(self):
        """Add at the end and highlight it."""
        self.prompt(lambda name: self.show(self.store.add_list(name)))

    def action_edit(self):
        if todo_list := self.current:
            self.prompt(lambda name: self.show(self.store.rename_list(todo_list.id, name)), todo_list.name)


class ListContentsPane[T: Record](RecordPane[T]):
    """Records of the list `list_id`, set by the app; none until it is."""

    BINDINGS = [Binding("left,h,escape", "app.focus_lists", "Lists", show=False)]

    def __init__(self, store: Store, **options):
        super().__init__(store, **options)
        self.list_id: UUID | None = None

    def load(self) -> list[T]:
        return self.fetch(self.list_id) if self.list_id else []

    def fetch(self, list_id: UUID) -> list[T]:
        raise NotImplementedError

    def on_option_list_option_selected(self):
        self.action_edit()


class TodosPane(ListContentsPane[Todo]):
    """Todos under a title per scheduled date; titles are disabled, so the cursor skips them."""

    BINDINGS = [
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        Binding("space", "toggle", "Done"),
        Binding("number_sign", "edit_labels", "Labels", key_display="#"),
        ("d", "delete", "Delete"),
        Binding("full_stop", "shift(1)", "+1 day", key_display="."),
        Binding("comma", "shift(-1)", "-1 day", key_display=","),
    ]

    def fetch(self, list_id: UUID) -> list[Todo]:
        return self.store.todos(list_id)

    def make_options(self) -> Iterator[Option]:
        for day, todos in sections(self.records):
            yield Option(Content(section_title(day)), disabled=True)
            yield from (Option(todo_label(t), str(t.id)) for t in todos)

    def shown(self) -> list[Todo]:
        return [t for _, todos in sections(self.records) for t in todos]

    def prompt(self, save: Callable[[str], object], value: str = "", **options):
        self.app.push_screen(TextPrompt(save, value, **options))

    def action_add(self):
        """Add below the highlighted todo on its date, or at the end for today if none is."""
        if not self.list_id:
            return
        index, fields = None, {}
        if current := self.current:
            index, fields = self.records.index(current) + 1, {"scheduled_at": current.scheduled_at}
        self.prompt(lambda text: self.show(self.store.add_todo(self.list_id, text, index, **fields)))

    def action_edit(self):
        if todo := self.current:
            self.prompt(lambda text: self.show(self.store.update_todo(todo.id, text=text)), todo.text)

    def action_edit_labels(self):
        """Edit as space-separated words, completing ones already in use."""
        if not (todo := self.current):
            return
        self.prompt(
            lambda value: self.show(self.store.update_todo(todo.id, labels=value.split())),
            "".join(f"{label} " for label in todo.labels),
            placeholder="Labels",
            suggester=LabelSuggester(self.store.labels()),
            select_on_focus=False,
        )

    def action_toggle(self):
        if todo := self.current:
            self.show(self.store.update_todo(todo.id, done=not todo.done))

    def action_delete(self):
        if todo := self.current:
            self.store.delete_todo(todo.id)
            self.show_without(todo)

    def action_shift(self, days: int):
        if todo := self.current:
            self.show(self.store.shift_todo(todo.id, days))


class RepeatsPane(ListContentsPane[Repeat]):
    BINDINGS = [("a", "add", "Add"), ("e", "edit", "Edit"), ("d", "delete", "Delete")]

    def fetch(self, list_id: UUID) -> list[Repeat]:
        return self.store.repeats(list_id)

    def label(self, repeat: Repeat) -> Content:
        return repeat_label(repeat)

    def action_add(self):
        if self.list_id:
            self.app.push_screen(RepeatDialog(lambda fields: self.show(self.store.add_repeat(self.list_id, **fields))))

    def action_edit(self):
        if repeat := self.current:
            self.app.push_screen(
                RepeatDialog(lambda changes: self.show(self.store.update_repeat(repeat.id, **changes)), repeat)
            )

    def action_delete(self):
        """Delete, keeping the todos it spawned."""
        if repeat := self.current:
            self.store.delete_repeat(repeat.id)
            self.show_without(repeat)
