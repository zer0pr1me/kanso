from uuid import UUID

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, OptionList
from textual.widgets.option_list import Option

from kanso.core.models import Todo
from kanso.core.store import Store


def todo_label(todo: Todo) -> str:
    label = f"{'[x]' if todo.done else '[ ]'} {todo.text}"
    return f"{label}  ({todo.scheduled_at:%Y-%m-%d})" if todo.scheduled_at else label


class KansoApp(App):
    TITLE = "kanso"
    CSS = """
    #lists { width: 30; }
    #todos { width: 1fr; }
    """

    def __init__(self, store: Store):
        super().__init__()
        self.store = store

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

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted):
        if event.option_list.id == "lists":
            todos = self.query_one("#todos", OptionList)
            todos.clear_options()
            todos.add_options(todo_label(t) for t in self.store.todos(UUID(event.option.id)))


def main():
    store = Store()
    try:
        KansoApp(store).run()
    finally:
        store.close()


if __name__ == "__main__":
    main()
