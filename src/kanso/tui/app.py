from datetime import date

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer, Header, OptionList

from kanso.core.store import Store
from kanso.tui.panes import ListContentsPane, ListsPane, RepeatsPane, TodosPane


class KansoApp(App):
    """Lists on the left; on the right the highlighted list's todos, or its repeats."""

    TITLE = "kanso"
    CSS = """
    #lists { width: 30; }
    #panes { width: 1fr; }
    #todos > .option-list--option-disabled { color: $text-accent; text-style: bold; }
    """

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.day = date.today()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListsPane(self.store, id="lists")
            with ContentSwitcher(id="panes", initial="todos"):
                yield TodosPane(self.store, id="todos")
                yield RepeatsPane(self.store, id="repeats")
        yield Footer()

    def on_mount(self):
        self.store.spawn_repeats(self.day)
        self.query_one(ListsPane).show()
        self.set_interval(60, self.check_day)

    def check_day(self):
        """Once the date changes, spawn the new day's repeats and redraw the dated sections."""
        if date.today() == self.day:
            return
        self.day = date.today()
        self.store.spawn_repeats(self.day)
        todos = self.query_one(TodosPane)
        todos.show(todos.current)

    def shown_pane(self) -> ListContentsPane:
        switcher = self.query_one("#panes", ContentSwitcher)
        return switcher.get_child_by_id(switcher.current, ListContentsPane)

    @on(OptionList.OptionHighlighted, "#lists")
    def show_list(self):
        list_id = self.query_one(ListsPane).current.id
        for pane in self.query(ListContentsPane):
            pane.list_id = list_id
            pane.show()

    @on(OptionList.OptionSelected, "#lists")
    def action_focus_pane(self):
        """Focus the shown pane, highlighting its first item if none is."""
        pane = self.shown_pane()
        if pane.highlighted is None:
            pane.action_first()
        pane.focus()

    def action_focus_lists(self):
        self.query_one(ListsPane).focus()

    def action_toggle_repeats(self):
        """Switch between the list's todos and repeats, reloading the one shown (repeats spawn
        todos) and keeping focus on that side."""
        switcher = self.query_one("#panes", ContentSwitcher)
        switcher.current = "todos" if switcher.current == "repeats" else "repeats"
        pane = self.shown_pane()
        pane.show(pane.current)
        if not self.query_one(ListsPane).has_focus:
            self.action_focus_pane()


def main():
    store = Store()
    try:
        KansoApp(store).run()
    finally:
        store.close()


if __name__ == "__main__":
    main()
