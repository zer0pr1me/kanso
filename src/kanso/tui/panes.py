"""The app's three panes: lists, and a list's todos or repeats.

Each pane binds its own keys, so they only act while it's focused and the footer shows
just those. Panes save through dialogs and redraw themselves from the store.
"""

from collections.abc import Callable, Iterator
from datetime import date
from uuid import UUID

from textual.binding import Binding
from textual.content import Content
from textual.message import Message
from textual.widgets.option_list import Option

from kanso.core.models import Record, Repeat, Todo, TodoList
from kanso.core.order import done_before, sections
from kanso.core.store import NAME_MAX, NotFound, Store
from kanso.tui.dialogs import ConfirmDialog, RepeatDialog, TextPrompt
from kanso.tui.format import repeat_label, section_title, todo_label
from kanso.tui.widgets import LabelSuggester, VimOptionList


def successor[T: Record](records: list[T], record: T) -> T | None:
    """What takes `record`'s place once it's removed: the next one, else the previous one."""
    rest = [r for r in records if r.id != record.id]
    index = next(i for i, r in enumerate(records) if r.id == record.id)
    return rest[min(index, len(rest) - 1)] if rest else None


class RecordPane[T: Record](VimOptionList):
    """Records from the store, one option each, with the option id being the record id."""

    BINDINGS = [("r", "app.toggle_repeats", "Todos/Repeats"), ("c", "app.connect", "Connect")]

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
        """Reload, highlighting `highlight` if given and still shown (sync may have deleted it)."""
        self.records = self.load()
        self.clear_options()
        self.add_options(self.make_options())
        if highlight and any(r.id == highlight.id for r in self.shown()):
            self.highlighted = self.get_option_index(str(highlight.id))

    def delete(self, record: T, question: str, delete: Callable[[UUID], object]):
        """If `question` is confirmed, `delete` the record and highlight the one now in its place."""

        def confirmed(yes: bool):
            if yes:
                delete(record.id)
                self.show(successor(self.shown(), record))

        self.app.push_screen(ConfirmDialog(question), confirmed)

    @property
    def current(self) -> T | None:
        """The highlighted record."""
        option = self.highlighted_option
        return next((r for r in self.records if option and str(r.id) == option.id), None)


def count(n: int, thing: str) -> str:
    return f"{n} {thing}{'' if n == 1 else 's'}"


class ListsPane(RecordPane[TodoList]):
    BINDINGS = [
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        ("d", "delete", "Delete"),
        Binding("right,l", "app.focus_pane", show=False),
    ]

    class Emptied(Message):
        """The last list is gone, so no list is highlighted to show."""

    def load(self) -> list[TodoList]:
        return self.store.lists()

    def label(self, todo_list: TodoList) -> Content:
        return Content(todo_list.name)

    def show(self, highlight: TodoList | None = None):
        """Reload, highlighting `highlight`, else the first list."""
        super().show(highlight)
        if not self.records:
            self.post_message(self.Emptied())
        elif not highlight:
            self.highlighted = 0

    def prompt(self, save: Callable[[str], object], name: str = ""):
        self.app.push_screen(TextPrompt(save, name, placeholder="List", max_length=NAME_MAX))

    def action_add(self):
        """Add at the end and highlight it."""
        self.prompt(lambda name: self.show(self.store.add_list(name)))

    def action_edit(self):
        if todo_list := self.current:
            self.prompt(lambda name: self.show(self.store.rename_list(todo_list.id, name)), todo_list.name)

    def action_delete(self):
        if todo_list := self.current:
            contents = [count(len(self.store.todos(todo_list.id)), "todo"), count(len(self.store.repeats(todo_list.id)), "repeat")]
            question = f'Delete list "{todo_list.name}" with its {" and ".join(contents)}?'
            self.delete(todo_list, question, self.store.delete_list)


class ListContentsPane[T: Record](RecordPane[T]):
    """Records of the list `list_id`, set by the app; none until it is, or once sync deleted it."""

    BINDINGS = [Binding("left,h,escape", "app.focus_lists", "Lists", show=False)]

    def __init__(self, store: Store, **options):
        super().__init__(store, **options)
        self.list_id: UUID | None = None

    def load(self) -> list[T]:
        try:
            return self.fetch(self.list_id) if self.list_id else []
        except NotFound:
            return []

    def fetch(self, list_id: UUID) -> list[T]:
        raise NotImplementedError

    def on_option_list_option_selected(self):
        self.action_edit()


SHOW_OLD_DONE = "show_old_done"
"""Setting of this replica: if set, todos done before today are shown too."""


class TodosPane(ListContentsPane[Todo]):
    """Todos under a title per scheduled date; titles are disabled, so the cursor skips them.
    Todos done before today are hidden unless `f` shows them; `records` keeps them all."""

    BINDINGS = [
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        Binding("space", "toggle", "Done"),
        Binding("number_sign", "edit_labels", "Labels", key_display="#"),
        ("d", "delete", "Delete"),
        Binding("full_stop", "shift(1)", "+1 day", key_display="."),
        Binding("comma", "shift(-1)", "-1 day", key_display=","),
        ("f", "toggle_old_done", "Old done"),
    ]

    def fetch(self, list_id: UUID) -> list[Todo]:
        return self.store.todos(list_id)

    def visible(self) -> list[Todo]:
        if self.store.meta(SHOW_OLD_DONE):
            return self.records
        today = date.today()
        return [t for t in self.records if not done_before(t, today)]

    def make_options(self) -> Iterator[Option]:
        visible = self.visible()
        for day, todos in sections(visible):
            yield Option(Content(section_title(day)), disabled=True)
            yield from (Option(todo_label(t), str(t.id)) for t in todos)
        if hidden := len(self.records) - len(visible):
            note = f"{count(hidden, 'todo')} done before today hidden · f shows"
            yield Option(Content.styled(note, "not bold dim italic"), disabled=True)

    def shown(self) -> list[Todo]:
        return [t for _, todos in sections(self.visible()) for t in todos]

    def action_toggle_old_done(self):
        """Show or hide todos done before today, keeping the highlight if it stays shown."""
        self.store.set_meta(SHOW_OLD_DONE, None if self.store.meta(SHOW_OLD_DONE) else "1")
        self.show(self.current)
        if self.highlighted is None:
            self.action_first()

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
        """Edit the text, or the repeat that spawned the todo, which sets it."""
        if not (todo := self.current):
            return
        if repeat := self.store.repeat_of(todo.id):
            self.app.edit_repeat(repeat)
        else:
            self.prompt(lambda text: self.show(self.store.update_todo(todo.id, text=text)), todo.title)

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
            self.delete(todo, f'Delete "{todo.title}"?', self.store.delete_todo)

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
        if repeat := self.current:
            question = f'Delete repeat "{repeat.text}"? The todos it added stay.'
            self.delete(repeat, question, self.store.delete_repeat)
