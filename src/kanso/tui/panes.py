"""The app's panes: lists, and a list's todos or repeats above its habit days or habits.

Each pane binds its own keys, so they only act while it's focused and the footer shows
just those. Panes save through dialogs and redraw themselves from the store.
"""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from itertools import groupby
from uuid import UUID

from textual.binding import Binding
from textual.content import Content
from textual.message import Message
from textual.widgets.option_list import Option

from kanso.core.models import Habit, HabitDay, Record, Repeat, Scheduled, Todo, TodoList
from kanso.core.order import done_before, sections
from kanso.core.store import NAME_MAX, NotFound, Store
from kanso.tui.dialogs import ConfirmDialog, RepeatDialog, TextPrompt
from kanso.tui.format import habit_day_label, human_date, section_title, template_label, todo_label
from kanso.tui.widgets import LabelSuggester, SectionedOptionList, SectionTitle, VimOptionList, layout_alias, with_layouts


def successor[T: Record](records: list[T], record: T) -> T | None:
    """What takes `record`'s place once it's removed: the next one, else the previous one."""
    rest = [r for r in records if r.id != record.id]
    index = next(i for i, r in enumerate(records) if r.id == record.id)
    return rest[min(index, len(rest) - 1)] if rest else None


MOVE_BINDINGS = [
    Binding("J,shift+down", "move(1)", "Move", key_display="J/K"),
    Binding("K,shift+up", "move(-1)", show=False),
]
TOP_TOGGLE = ("r", "app.toggle_templates", "Todos/Repeats")
BOTTOM_TOGGLE = ("r", "app.toggle_templates", "Days/Habits")


class RecordPane[T: Record](VimOptionList):
    """Records from the store, one option each, with the option id being the record id."""

    BINDINGS = [*with_layouts([("c", "app.connect", "Connect")]), Binding("question_mark", "app.help", "Help", key_display="?")]
    """`?` has no copy for the Ukrainian layout: it types `?` too, and its copy would be the comma."""

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
        """Reload, highlighting `highlight` if given and still shown (sync may have deleted it),
        keeping the scroll position unless the highlight would be out of view."""
        self.records, scrolled = self.load(), self.scroll_y
        self.clear_options()
        self.add_options(self.make_options())
        self.scroll_to(y=scrolled, animate=False, immediate=True)
        if highlight and any(r.id == highlight.id for r in self.shown()):
            self.highlighted = self.get_option_index(str(highlight.id))

    def group(self, record: T) -> list[T]:
        """The records shown that `record` can move among, in order."""
        return self.shown()

    def move(self, record: T, index: int) -> T:
        """Move `record` to `index` in the store's order; saved and returned."""
        raise NotImplementedError

    def action_move(self, step: int):
        """Swap the highlighted record with the next (`step` 1) or previous (-1) of its group;
        at the group's edge, stay."""
        if not (record := self.current):
            return
        group = self.group(record)
        place = next(i for i, r in enumerate(group) if r.id == record.id) + step
        if not 0 <= place < len(group):
            return
        others = [r for r in self.records if r.id != record.id]
        index = next(i for i, r in enumerate(others) if r.id == group[place].id)
        self.show(self.move(record, index + (step > 0)))

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
    BINDINGS = with_layouts([
        TOP_TOGGLE,
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        ("d", "delete", "Delete"),
        *MOVE_BINDINGS,
        Binding("right,l", "app.focus_pane", show=False),
        Binding("escape", "app.quit", "Quit"),  # on the right, escape comes back here first
    ])

    class Emptied(Message):
        """The last list is gone, so no list is highlighted to show."""

    def load(self) -> list[TodoList]:
        return self.store.lists()

    def move(self, todo_list: TodoList, index: int) -> TodoList:
        return self.store.move_list(todo_list.id, index)

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
        if not (todo_list := self.current):
            return
        found = [self.store.todos, self.store.repeats, self.store.habits]
        contents = [count(n, thing) for find, thing in zip(found, ["todo", "repeat", "habit"]) if (n := len(find(todo_list.id)))]
        listed = " and ".join(filter(None, [", ".join(contents[:-1]), *contents[-1:]]))
        question = f'Delete list "{todo_list.name}"{f" with its {listed}" if listed else ""}?'
        self.delete(todo_list, question, self.store.delete_list)


class ListContentsPane[T: Record](RecordPane[T]):
    """Records of the list `list_id`, set by the app; none until it is, or once sync deleted it."""

    BINDINGS = with_layouts([
        Binding("left,h,escape", "app.focus_lists", "Lists", show=False),
        Binding("F", "app.fullscreen", "Fullscreen"),
    ])

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


class TodosPane(ListContentsPane[Todo], SectionedOptionList):
    """Todos under a title per scheduled date; titles are disabled, so the cursor skips them.
    Todos done before today are hidden unless `f` shows them; `records` keeps them all."""

    BINDINGS = with_layouts([
        TOP_TOGGLE,
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        ("y", "duplicate", "Duplicate"),
        Binding("space", "toggle", "Done"),
        Binding("number_sign", "edit_labels", "Labels", key_display="#"),
        ("d", "delete", "Delete"),
        Binding("full_stop", "shift(1)", "+1 day", key_display="."),
        Binding("comma", "shift(-1)", "-1 day", key_display=","),
        *MOVE_BINDINGS,
        ("f", "toggle_old_done", "Old done"),
    ])

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
            yield SectionTitle(section_title(day))
            yield from (Option(todo_label(t), str(t.id)) for t in todos)
        if hidden := len(self.records) - len(visible):
            note = f"{count(hidden, 'todo')} done before today hidden · f shows"
            yield Option(Content.styled(note, "not bold dim italic"), disabled=True)

    def shown(self) -> list[Todo]:
        return [t for _, todos in sections(self.visible()) for t in todos]

    def group(self, todo: Todo) -> list[Todo]:
        """Todos of the same date and the same done or not, as autosort keeps groups apart."""
        return [t for t in self.shown() if (t.scheduled_at, t.done) == (todo.scheduled_at, todo.done)]

    def move(self, todo: Todo, index: int) -> Todo:
        return self.store.move_todo(todo.id, index)

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
            self.app.edit_template("repeats", repeat)
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

    def action_duplicate(self):
        """Copy below and highlight the copy; repeating todos are refused with a notification."""
        if not (todo := self.current):
            return
        try:
            self.show(self.store.duplicate_todo(todo.id))
        except ValueError as error:
            self.notify(str(error), severity="error")

    def action_toggle(self):
        if todo := self.current:
            self.show(self.store.update_todo(todo.id, done=not todo.done))

    def action_delete(self):
        if todo := self.current:
            self.delete(todo, f'Delete "{todo.title}"?', self.store.delete_todo)

    def action_shift(self, days: int):
        if todo := self.current:
            self.show(self.store.shift_todo(todo.id, days))


TEMPLATE_BINDINGS = [("a", "add", "Add"), ("e", "edit", "Edit"), ("d", "delete", "Delete"), *MOVE_BINDINGS]


class TemplatesPane[T: Scheduled](ListContentsPane[T]):
    """A list's templates, repeats or habits, edited with `RepeatDialog`."""

    placeholder: str
    """What the dialog's text field asks for."""

    def add(self, fields: dict) -> T:
        raise NotImplementedError

    def update(self, template: T, changes: dict) -> T:
        raise NotImplementedError

    def remove(self, template_id: UUID):
        raise NotImplementedError

    def question(self, template: T) -> str:
        """What deleting asks."""
        raise NotImplementedError

    def label(self, template: T) -> Content:
        return template_label(template)

    def action_add(self):
        if self.list_id:
            self.app.push_screen(RepeatDialog(lambda fields: self.show(self.add(fields)), placeholder=self.placeholder))

    def action_edit(self):
        if template := self.current:
            self.app.push_screen(
                RepeatDialog(lambda changes: self.show(self.update(template, changes)), template, self.placeholder)
            )

    def action_delete(self):
        if template := self.current:
            self.delete(template, self.question(template), self.remove)


class RepeatsPane(TemplatesPane[Repeat]):
    BINDINGS = with_layouts([TOP_TOGGLE, *TEMPLATE_BINDINGS])
    placeholder = "Repeating todo"

    def fetch(self, list_id: UUID) -> list[Repeat]:
        return self.store.repeats(list_id)

    def move(self, repeat: Repeat, index: int) -> Repeat:
        return self.store.move_repeat(repeat.id, index)

    def add(self, fields: dict) -> Repeat:
        return self.store.add_repeat(self.list_id, **fields)

    def update(self, repeat: Repeat, changes: dict) -> Repeat:
        return self.store.update_repeat(repeat.id, **changes)

    def remove(self, repeat_id: UUID):
        self.store.delete_repeat(repeat_id)

    def question(self, repeat: Repeat) -> str:
        return f'Delete repeat "{repeat.text}"? The todos it added stay.'


class HabitsPane(TemplatesPane[Habit]):
    BINDINGS = with_layouts([BOTTOM_TOGGLE, *TEMPLATE_BINDINGS])
    placeholder = "Habit"

    def fetch(self, list_id: UUID) -> list[Habit]:
        return self.store.habits(list_id)

    def move(self, habit: Habit, index: int) -> Habit:
        return self.store.move_habit(habit.id, index)

    def add(self, fields: dict) -> Habit:
        return self.store.add_habit(self.list_id, **fields)

    def update(self, habit: Habit, changes: dict) -> Habit:
        return self.store.update_habit(habit.id, **changes)

    def remove(self, habit_id: UUID):
        self.store.delete_habit(habit_id)

    def question(self, habit: Habit) -> str:
        return f'Delete habit "{habit.text}"? Its days stay.'


SHOW_OLD_DAYS = "show_old_habit_days"
"""Setting of this replica: if set, habit days older than a week are shown too."""
RECENT_DAYS = 7


class HabitDaysPane(ListContentsPane[HabitDay], SectionedOptionList):
    """Habit days under a title per date, newest first; only the last week's unless `f` shows
    older ones. A day is marked once it's over: `space` a success, `x` a failure, again clears."""

    BINDINGS = with_layouts([
        BOTTOM_TOGGLE,
        ("a", "add", "Add habit"),
        Binding("space", "mark('success')", "Success"),
        Binding("x", "mark('failure')", "Failure"),
        ("e", "edit", "Edit habit"),
        ("d", "delete", "Delete"),
        ("f", "toggle_old", "Older days"),
    ])

    def fetch(self, list_id: UUID) -> list[HabitDay]:
        return self.store.habit_days(list_id)

    def visible(self) -> list[HabitDay]:
        if self.store.meta(SHOW_OLD_DAYS):
            return self.records
        since = date.today() - timedelta(days=RECENT_DAYS - 1)
        return [d for d in self.records if d.day >= since]

    def make_options(self) -> Iterator[Option]:
        visible = self.visible()
        for day, days in groupby(visible, lambda d: d.day):
            yield SectionTitle(section_title(day))
            yield from (Option(habit_day_label(d), str(d.id)) for d in days)
        if hidden := len(self.records) - len(visible):
            note = f"{count(hidden, 'older day')} hidden · f shows"
            yield Option(Content.styled(note, "not bold dim italic"), disabled=True)

    def shown(self) -> list[HabitDay]:
        return self.visible()

    def action_toggle_old(self):
        """Show or hide days older than a week, keeping the highlight if it stays shown."""
        self.store.set_meta(SHOW_OLD_DAYS, None if self.store.meta(SHOW_OLD_DAYS) else "1")
        self.show(self.current)
        if self.highlighted is None:
            self.action_first()

    def action_mark(self, state: str):
        """Mark the day `state`, or unchecked if it already is."""
        if not (habit_day := self.current):
            return
        try:
            self.show(self.store.mark_habit_day(habit_day.id, None if habit_day.state == state else state))
        except ValueError as error:
            self.notify(str(error), severity="error")

    def action_add(self):
        """Switch to the habits and add one: days only come from habits."""
        if self.list_id:
            self.app.add_template("habits")

    def action_edit(self):
        """Edit the day's habit."""
        if not (habit_day := self.current):
            return
        if habit := self.store.habit_of(habit_day.id):
            self.app.edit_template("habits", habit)
        else:
            self.notify("Its habit was deleted", severity="error")

    def action_delete(self):
        if habit_day := self.current:
            question = f'Delete "{habit_day.title}" of {human_date(habit_day.day)}?'
            self.delete(habit_day, question, self.store.delete_habit_day)


def key_rows(pane: type[RecordPane], display: Callable[[Binding], str]) -> list[tuple[str, str]]:
    """What `pane`'s keys do, as (keys, description) rows: its own bindings and its bases' (but
    not Textual's), without the Ukrainian layout's copies; a key bound again by a subclass
    keeps the subclass's. `display` formats a binding's keys."""
    bindings = [b for cls in pane.__mro__ if cls.__module__.startswith("kanso") for b in cls.__dict__.get("BINDINGS", [])]
    aliases = {(b.action, layout_alias(b)) for b in bindings if layout_alias(b)}
    rows: dict[str, tuple[str, str]] = {}
    for binding in bindings:
        if (binding.action, binding.key) not in aliases and binding.description and binding.key not in rows:
            rows[binding.key] = (display(binding), binding.description)
    return list(rows.values())


HELP_PANES = {"Lists": ListsPane, "Todos": TodosPane, "Repeats": RepeatsPane, "Habit days": HabitDaysPane, "Habits": HabitsPane}
"""The panes the help describes, by title: a page each."""
