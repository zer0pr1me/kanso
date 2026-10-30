import calendar
from datetime import date
from itertools import groupby
from uuid import UUID

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import (
    ContentSwitcher,
    Footer,
    Header,
    Input,
    OptionList,
    RadioButton,
    RadioSet,
    SelectionList,
)
from textual.widgets.option_list import Option

from kanso.core.models import Record, Repeat, Todo, TodoList
from kanso.core.repeats import RULE_FIELDS, clean_rule
from kanso.core.store import NAME_MAX, Store, clean_text


def human_date(day: date, today: date | None = None) -> str:
    """Today/Tomorrow/Yesterday; a weekday within this week (Monday to Sunday), prefixed
    Next/Last in the adjacent weeks; else month and day, plus the year if not this one."""
    today = today or date.today()
    match (day - today).days:
        case 0:
            return "Today"
        case 1:
            return "Tomorrow"
        case -1:
            return "Yesterday"
    weeks = (day.toordinal() - day.weekday() - today.toordinal() + today.weekday()) // 7
    prefix = {-1: "Last ", 0: "", 1: "Next "}.get(weeks)
    if prefix is not None:
        return f"{prefix}{day:%A}"
    return f"{day:%b} {day.day}" + ("" if day.year == today.year else f", {day.year}")


LABEL_STYLE = "bold $text-accent on $accent-muted"
RULE_STYLE = "$text-secondary on $secondary-muted"


def todo_label(todo: Todo) -> Content:
    """Checkbox and text, ↻ if spawned by a repeat, then each label as a `#label` chip, as plain text."""
    chips = [part for label in todo.labels for part in (" ", (f" #{label} ", LABEL_STYLE))]
    repeat = " ↻" if todo.repeat_id else ""
    return Content.assemble(f"{'[x]' if todo.done else '[ ]'} {todo.text}{repeat}", *chips)


def describe_rule(repeat: Repeat) -> str:
    match repeat.kind:
        case "days":
            return "Daily" if repeat.interval == 1 else f"Every {repeat.interval} days"
        case "weekdays":
            return ", ".join(calendar.day_abbr[d] for d in repeat.weekdays)
        case _:
            return "Monthly on " + ", ".join(map(str, repeat.monthdays))


def repeat_label(repeat: Repeat) -> Content:
    """Text, then the rule as a chip, as plain text."""
    return Content.assemble(repeat.text, " ", (f" {describe_rule(repeat)} ", RULE_STYLE))


def section_title(day: date | None) -> str:
    return human_date(day) if day else "No date"


def sections(todos: list[Todo]) -> list[tuple[date | None, list[Todo]]]:
    """Todos grouped by scheduled date, earliest first and unscheduled last, each group autosorted:
    done todos first, otherwise in list order."""
    by_date = sorted(todos, key=lambda t: (t.scheduled_at is None, t.scheduled_at or date.min, not t.done))
    return [(day, list(group)) for day, group in groupby(by_date, lambda t: t.scheduled_at)]


def index_of(records: list[Record], record: Record) -> int:
    return next(i for i, r in enumerate(records) if r.id == record.id)


def successor(records: list[Record], record: Record) -> Record | None:
    """What takes `record`'s place once it's removed: the next one, else the previous one."""
    rest = [r for r in records if r.id != record.id]
    return rest[min(index_of(records, record), len(rest) - 1)] if rest else None


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


class TextPrompt(ModalScreen[str | None]):
    """Asks for a line of text; dismisses with it stripped, or `None` if cancelled."""

    BINDINGS = [("escape", "dismiss", "Cancel"), Binding("tab", "complete", show=False)]
    CSS = """
    TextPrompt { align: center middle; }
    TextPrompt Input { width: 60; }
    """

    def __init__(self, value: str = "", **options):
        """`options` go to the `Input`; the placeholder defaults to "Todo"."""
        super().__init__()
        self.input = Input(value, **{"placeholder": "Todo", **options})

    def compose(self) -> ComposeResult:
        yield self.input

    def action_complete(self):
        """Accept the suggestion, if any (right arrow does too)."""
        self.input.action_cursor_right()

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss(event.value.strip())


VIM_BINDINGS = [
    Binding("j", "cursor_down", show=False),
    Binding("k", "cursor_up", show=False),
    Binding("g", "first", show=False),
    Binding("G", "last", show=False),
]


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


REPEAT_KINDS = {"daily": "Daily", "days": "Every N days", "weekdays": "Weekdays", "monthdays": "Days of month"}


class RepeatDialog(ModalScreen[tuple[str, dict] | None]):
    """Asks for a repeat's text and rule, showing only the chosen kind's field.

    Enter in a text field, escape or ctrl+s saves: dismisses with the text and rule validated,
    or reports what's invalid and stays open. Dismisses with `None` instead when nothing
    changed, or when adding with blank text.
    """

    BINDINGS = [Binding("escape,ctrl+s", "save", "Save")]
    CSS = """
    RepeatDialog { align: center middle; }
    RepeatDialog > Vertical { width: 60; height: auto; border: round $accent; background: $surface; }
    RepeatDialog ContentSwitcher, RepeatDialog SelectionList { height: auto; }
    """

    def __init__(self, repeat: Repeat | None = None):
        super().__init__()
        self.repeat = repeat
        r = repeat or Repeat(text="", position="")
        kind = "daily" if r.kind == "days" and r.interval == 1 else r.kind
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

    def kind(self) -> str:
        return self.kinds.pressed_button.id.removeprefix("kind-")

    def rule(self) -> dict:
        match self.kind():
            case "daily":
                return {"kind": "days"}
            case "days":
                return {"kind": "days", "interval": int(self.interval.value or 0)}
            case "weekdays":
                return {"kind": "weekdays", "weekdays": list(self.weekdays.selected)}
            case _:
                return {"kind": "monthdays", "monthdays": [int(d) for d in self.monthdays.value.split()]}

    def on_radio_set_changed(self):
        self.fields.current = self.field_for(self.kind())

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self.action_save()

    def action_save(self):
        if not (self.repeat or self.text.value.strip()):
            self.dismiss(None)
            return
        try:
            result = clean_text(self.text.value), clean_rule(**self.rule())
        except ValueError as error:
            self.notify(str(error), severity="error")
            return
        r = self.repeat
        unchanged = r and result == (r.text, {field: getattr(r, field) for field in RULE_FIELDS})
        self.dismiss(None if unchanged else result)


class VimOptionList(OptionList):
    BINDINGS = VIM_BINDINGS


class KansoApp(App):
    TITLE = "kanso"
    CSS = """
    #lists { width: 30; }
    #panes { width: 1fr; }
    #todos > .option-list--option-disabled { color: $text-accent; text-style: bold; }
    """
    BINDINGS = [
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        Binding("space", "toggle_todo", "Done"),
        Binding("number_sign", "edit_labels", "Labels", key_display="#"),
        ("d", "delete", "Delete"),
        Binding("full_stop", "shift_date(1)", "+1 day", key_display="."),
        Binding("comma", "shift_date(-1)", "-1 day", key_display=","),
        ("r", "toggle_repeats", "Todos/Repeats"),
        Binding("right,l", "focus_pane", "Todos", show=False),
        Binding("left,h,escape", "focus_lists", "Lists", show=False),
    ]
    TODO_ACTIONS = {"toggle_todo", "edit_labels", "shift_date"}
    PANE_ACTIONS = TODO_ACTIONS | {"add", "edit", "delete", "toggle_repeats", "focus_pane", "focus_lists"}

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.list_id: UUID | None = None
        self.lists: list[TodoList] = []
        self.todos: list[Todo] = []
        self.repeats: list[Repeat] = []
        self.day = date.today()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield VimOptionList(id="lists")
            with ContentSwitcher(id="panes", initial="todos"):
                yield VimOptionList(id="todos")
                yield VimOptionList(id="repeats")
        yield Footer()

    def on_mount(self):
        self.store.spawn_repeats(self.day)
        self.show_lists()
        self.set_interval(60, self.check_day)

    def check_day(self):
        """Once the date changes, spawn the new day's repeats and redraw the dated sections."""
        if date.today() == self.day:
            return
        self.day = date.today()
        self.store.spawn_repeats(self.day)
        if self.list_id:
            self.show_todos(self.shown(self.query_one("#todos", OptionList), self.todos))

    def check_action(self, action: str, parameters: tuple) -> bool:
        """Pane actions only from the main screen, todo actions only while todos are shown."""
        if action in self.PANE_ACTIONS and self.screen is not self.screen_stack[0]:
            return False
        return not (action in self.TODO_ACTIONS and self.showing_repeats())

    def show_lists(self, highlight: TodoList | None = None):
        """Reload the lists, highlighting `highlight`, else the first one."""
        self.lists = self.store.lists()
        widget = self.query_one("#lists", OptionList)
        widget.clear_options()
        widget.add_options(Option(Content(l.name), str(l.id)) for l in self.lists)
        if self.lists:
            widget.highlighted = index_of(self.lists, highlight) if highlight else 0

    def show_todos(self, highlight: Todo | None = None):
        """Reload the todos under a title per scheduled date; titles are disabled, so the cursor skips them."""
        self.todos = self.store.todos(self.list_id)
        widget = self.query_one("#todos", OptionList)
        widget.clear_options()
        for day, todos in sections(self.todos):
            widget.add_option(Option(Content(section_title(day)), disabled=True))
            widget.add_options(Option(todo_label(t), str(t.id)) for t in todos)
        if highlight:
            widget.highlighted = widget.get_option_index(str(highlight.id))

    def show_repeats(self, highlight: Repeat | None = None):
        """Reload the repeats, highlighting `highlight` if given."""
        self.repeats = self.store.repeats(self.list_id)
        widget = self.query_one("#repeats", OptionList)
        widget.clear_options()
        widget.add_options(Option(repeat_label(r), str(r.id)) for r in self.repeats)
        if highlight:
            widget.highlighted = index_of(self.repeats, highlight)

    @staticmethod
    def shown[T: Record](widget: OptionList, records: list[T]) -> T | None:
        option = widget.highlighted_option
        return next((r for r in records if option and str(r.id) == option.id), None)

    def highlighted_todo(self) -> Todo | None:
        """The todo actions apply to: only while the todos pane is focused."""
        widget = self.query_one("#todos", OptionList)
        return self.shown(widget, self.todos) if widget.has_focus else None

    def highlighted_repeat(self) -> Repeat | None:
        """The repeat actions apply to: only while the repeats pane is focused."""
        widget = self.query_one("#repeats", OptionList)
        return self.shown(widget, self.repeats) if widget.has_focus else None

    def showing_repeats(self) -> bool:
        return self.query_one("#panes", ContentSwitcher).current == "repeats"

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted):
        if event.option_list.id == "lists":
            self.list_id = UUID(event.option.id)
            self.show_todos()
            self.show_repeats()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if event.option_list.id == "lists":
            self.action_focus_pane()
        elif event.option_list.id == "repeats":
            self.edit_repeat()
        else:
            self.edit_todo()

    def action_focus_pane(self):
        """Focus the shown pane, highlighting its first item if none is."""
        pane = self.query_one("#panes", ContentSwitcher)
        widget = pane.query_one(f"#{pane.current}", OptionList)
        if widget.highlighted is None:
            widget.action_first()
        widget.focus()

    def action_toggle_repeats(self):
        """Switch the right pane between todos and repeats, keeping focus on that side."""
        pane = self.query_one("#panes", ContentSwitcher)
        pane.current = "todos" if self.showing_repeats() else "repeats"
        if not self.lists_focused():
            self.action_focus_pane()
        self.refresh_bindings()

    def action_focus_lists(self):
        self.query_one("#lists").focus()

    def lists_focused(self) -> bool:
        return self.query_one("#lists").has_focus

    def action_add(self):
        if self.lists_focused():
            self.add_list()
        elif self.showing_repeats():
            self.add_repeat()
        else:
            self.add_todo()

    def action_edit(self):
        if self.lists_focused():
            self.edit_list()
        elif self.showing_repeats():
            self.edit_repeat()
        else:
            self.edit_todo()

    def action_delete(self):
        self.delete_repeat() if self.showing_repeats() else self.delete_todo()

    def list_prompt(self, value: str = "") -> TextPrompt:
        return TextPrompt(value, placeholder="List", max_length=NAME_MAX)

    def add_list(self):
        """Add at the end and highlight it."""

        def add(name: str | None):
            if name:
                self.show_lists(self.store.add_list(name))

        self.push_screen(self.list_prompt(), add)

    def edit_list(self):
        widget = self.query_one("#lists", OptionList)
        if widget.highlighted is None:
            return
        todo_list = self.lists[widget.highlighted]

        def edit(name: str | None):
            if name:
                self.show_lists(self.store.rename_list(todo_list.id, name))

        self.push_screen(self.list_prompt(todo_list.name), edit)

    def add_todo(self):
        """Add below the highlighted todo on its date, or at the end for today if none is."""
        if not self.list_id:
            return
        current = self.highlighted_todo()
        index, fields = None, {}
        if current:
            index, fields = self.todos.index(current) + 1, {"scheduled_at": current.scheduled_at}

        def add(text: str | None):
            if text:
                self.show_todos(self.store.add_todo(self.list_id, text, index, **fields))
                self.query_one("#todos").focus()

        self.push_screen(TextPrompt(), add)

    def edit_todo(self):
        todo = self.highlighted_todo()
        if not todo:
            return

        def edit(text: str | None):
            if text:
                self.show_todos(self.store.update_todo(todo.id, text=text))

        self.push_screen(TextPrompt(todo.text), edit)

    def action_edit_labels(self):
        """Edit as space-separated words, completing ones already in use."""
        if not (todo := self.highlighted_todo()):
            return

        def edit(value: str | None):
            if value is None:
                return
            try:
                self.show_todos(self.store.update_todo(todo.id, labels=value.split()))
            except ValueError as error:
                self.notify(str(error), severity="error")

        value = "".join(f"{label} " for label in todo.labels)
        suggester = LabelSuggester(self.store.labels())
        self.push_screen(TextPrompt(value, placeholder="Labels", suggester=suggester, select_on_focus=False), edit)

    def add_repeat(self):
        """Append to the list's repeats, spawning its todo if due today."""
        if not self.list_id:
            return

        def add(result: tuple[str, dict] | None):
            if result:
                text, rule = result
                repeat = self.store.add_repeat(self.list_id, text, **rule)
                self.store.spawn_repeats(self.day)
                self.show_repeats(repeat)
                self.show_todos()
                self.query_one("#repeats").focus()

        self.push_screen(RepeatDialog(), add)

    def edit_repeat(self):
        """Edit text and rule, spawning its todo if the new rule is due today."""
        if not (repeat := self.highlighted_repeat()):
            return

        def edit(result: tuple[str, dict] | None):
            if result:
                text, rule = result
                self.show_repeats(self.store.update_repeat(repeat.id, text, **rule))
                self.store.spawn_repeats(self.day)
                self.show_todos()

        self.push_screen(RepeatDialog(repeat), edit)

    def delete_repeat(self):
        """Delete, keeping its todos, then highlight the next repeat, or the previous if it was last."""
        if not (repeat := self.highlighted_repeat()):
            return
        self.store.delete_repeat(repeat.id)
        self.show_repeats(successor(self.repeats, repeat))
        self.show_todos()

    def action_toggle_todo(self):
        if todo := self.highlighted_todo():
            self.show_todos(self.store.update_todo(todo.id, done=not todo.done))

    def delete_todo(self):
        """Delete, then highlight the next todo shown, or the previous one if it was last."""
        if not (todo := self.highlighted_todo()):
            return
        shown = [t for _, todos in sections(self.todos) for t in todos]
        self.store.delete_todo(todo.id)
        self.show_todos(successor(shown, todo))

    def action_shift_date(self, days: int):
        if todo := self.highlighted_todo():
            self.show_todos(self.store.shift_todo(todo.id, days))


def main():
    store = Store()
    try:
        KansoApp(store).run()
    finally:
        store.close()


if __name__ == "__main__":
    main()
