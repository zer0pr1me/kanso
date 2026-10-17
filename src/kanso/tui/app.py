from uuid import UUID

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, OptionList
from textual.widgets.option_list import Option

from kanso.core.models import Todo
from kanso.core.store import Store


def todo_label(todo: Todo) -> str:
    label = f"{'[x]' if todo.done else '[ ]'} {todo.text}"
    return f"{label}  ({todo.scheduled_at:%Y-%m-%d})" if todo.scheduled_at else label


class TextPrompt(ModalScreen[str | None]):
    """Asks for a line of text; dismisses with it stripped, or `None` if blank or cancelled."""

    BINDINGS = [("escape", "dismiss", "Cancel")]
    CSS = """
    TextPrompt { align: center middle; }
    TextPrompt Input { width: 60; }
    """

    def __init__(self, value: str = ""):
        super().__init__()
        self.value = value

    def compose(self) -> ComposeResult:
        yield Input(self.value, placeholder="Todo")

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss(event.value.strip() or None)


class KansoApp(App):
    TITLE = "kanso"
    CSS = """
    #lists { width: 30; }
    #todos { width: 1fr; }
    """
    BINDINGS = [("a", "add_todo", "Add"), ("e", "edit_todo", "Edit")]

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.list_id: UUID | None = None
        self.todos: list[Todo] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield OptionList(*(Option(l.name, str(l.id)) for l in self.store.lists()), id="lists")
            yield OptionList(id="todos")
        yield Footer()

    def on_mount(self):
        lists = self.query_one("#lists", OptionList)
        if lists.option_count:
            lists.highlighted = 0

    def show_todos(self, highlight: Todo | None = None):
        self.todos = self.store.todos(self.list_id)
        widget = self.query_one("#todos", OptionList)
        widget.clear_options()
        widget.add_options(todo_label(t) for t in self.todos)
        if highlight:
            widget.highlighted = next(i for i, t in enumerate(self.todos) if t.id == highlight.id)

    def highlighted_todo(self) -> Todo | None:
        index = self.query_one("#todos", OptionList).highlighted
        return None if index is None else self.todos[index]

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted):
        if event.option_list.id == "lists":
            self.list_id = UUID(event.option.id)
            self.show_todos()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if event.option_list.id == "todos":
            self.action_edit_todo()

    def action_add_todo(self):
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

    def action_edit_todo(self):
        todo = self.highlighted_todo()
        if not todo:
            return

        def edit(text: str | None):
            if text:
                self.show_todos(self.store.update_todo(todo.id, text=text))

        self.push_screen(TextPrompt(todo.text), edit)


def main():
    store = Store()
    try:
        KansoApp(store).run()
    finally:
        store.close()


if __name__ == "__main__":
    main()
