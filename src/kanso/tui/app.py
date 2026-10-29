from datetime import date
from uuid import UUID

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, OptionList
from textual.widgets.option_list import Option

from kanso.core.models import Record, Todo, TodoList
from kanso.core.store import NAME_MAX, Store


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


def todo_label(todo: Todo) -> Content:
    """Scheduled date in an aligned column (blank if none), then checkbox and text, as plain text."""
    scheduled = human_date(todo.scheduled_at) if todo.scheduled_at else ""
    return Content(f"{scheduled:14}  {'[x]' if todo.done else '[ ]'} {todo.text}")


def index_of(records: list[Record], record: Record) -> int:
    return next(i for i, r in enumerate(records) if r.id == record.id)


class TextPrompt(ModalScreen[str | None]):
    """Asks for a line of text; dismisses with it stripped, or `None` if blank or cancelled."""

    BINDINGS = [("escape", "dismiss", "Cancel")]
    CSS = """
    TextPrompt { align: center middle; }
    TextPrompt Input { width: 60; }
    """

    def __init__(self, value: str = "", placeholder: str = "Todo", max_length: int = 0):
        super().__init__()
        self.input = Input(value, placeholder=placeholder, max_length=max_length)

    def compose(self) -> ComposeResult:
        yield self.input

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss(event.value.strip() or None)


class VimOptionList(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g", "first", show=False),
        Binding("G", "last", show=False),
    ]


class KansoApp(App):
    TITLE = "kanso"
    CSS = """
    #lists { width: 30; }
    #todos { width: 1fr; }
    """
    BINDINGS = [
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        Binding("space", "toggle_todo", "Done"),
        ("d", "delete_todo", "Delete"),
        Binding("full_stop", "shift_date(1)", "+1 day", key_display="."),
        Binding("comma", "shift_date(-1)", "-1 day", key_display=","),
        Binding("right,l", "focus_todos", "Todos", show=False),
        Binding("left,h,escape", "focus_lists", "Lists", show=False),
    ]

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.list_id: UUID | None = None
        self.lists: list[TodoList] = []
        self.todos: list[Todo] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield VimOptionList(id="lists")
            yield VimOptionList(id="todos")
        yield Footer()

    def on_mount(self):
        self.show_lists()

    def show_lists(self, highlight: TodoList | None = None):
        """Reload the lists, highlighting `highlight`, else the first one."""
        self.lists = self.store.lists()
        widget = self.query_one("#lists", OptionList)
        widget.clear_options()
        widget.add_options(Option(Content(l.name), str(l.id)) for l in self.lists)
        if self.lists:
            widget.highlighted = index_of(self.lists, highlight) if highlight else 0

    def show_todos(self, highlight: Todo | None = None):
        self.todos = self.store.todos(self.list_id)
        widget = self.query_one("#todos", OptionList)
        widget.clear_options()
        widget.add_options(todo_label(t) for t in self.todos)
        if highlight:
            widget.highlighted = index_of(self.todos, highlight)

    def highlighted_todo(self) -> Todo | None:
        """The todo actions apply to: only while the todos pane is focused."""
        widget = self.query_one("#todos", OptionList)
        return None if widget.highlighted is None or not widget.has_focus else self.todos[widget.highlighted]

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted):
        if event.option_list.id == "lists":
            self.list_id = UUID(event.option.id)
            self.show_todos()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if event.option_list.id == "lists":
            self.action_focus_todos()
        else:
            self.edit_todo()

    def action_focus_todos(self):
        todos = self.query_one("#todos", OptionList)
        if todos.highlighted is None and todos.option_count:
            todos.highlighted = 0
        todos.focus()

    def action_focus_lists(self):
        self.query_one("#lists").focus()

    def lists_focused(self) -> bool:
        return self.query_one("#lists").has_focus

    def action_add(self):
        self.add_list() if self.lists_focused() else self.add_todo()

    def action_edit(self):
        self.edit_list() if self.lists_focused() else self.edit_todo()

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
        """Add below the highlighted todo, or at the end if none is."""
        if not self.list_id:
            return
        current = self.highlighted_todo()
        index = None if current is None else self.todos.index(current) + 1

        def add(text: str | None):
            if text:
                self.show_todos(self.store.add_todo(self.list_id, text, index))
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

    def action_toggle_todo(self):
        if todo := self.highlighted_todo():
            self.show_todos(self.store.update_todo(todo.id, done=not todo.done))

    def action_delete_todo(self):
        """Delete, then highlight the next todo, or the previous one if it was last."""
        if not (todo := self.highlighted_todo()):
            return
        index = self.todos.index(todo)
        rest = [t for t in self.todos if t is not todo]
        self.store.delete_todo(todo.id)
        self.show_todos(rest[min(index, len(rest) - 1)] if rest else None)

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
