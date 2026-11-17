"""The habit statistics screen."""

from datetime import date

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from kanso.node import NodeView
from kanso.tui.format import habit_grid
from kanso.tui.widgets import with_layouts


class HabitStats(Screen):
    """Every habit's marks over the last weeks, by list, with its streak and success rate
    (`format.habit_grid`); `h`/`l` page a week earlier or later. Redraws on changes from
    elsewhere and on resizing."""

    BINDINGS = with_layouts([
        Binding("h,left", "page(1)", "Earlier", key_display="h/l"),
        Binding("l,right", "page(-1)", "Later", show=False),
        Binding("escape,q,s", "app.pop_screen", "Back"),
    ])
    CSS = "HabitStats #grid { padding: 1 2; }"

    def __init__(self, node: NodeView):
        super().__init__()
        self.node, self.back = node, 0
        self.unsubscribe = lambda: None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(id="grid")
        yield Footer()

    def on_mount(self):
        self.unsubscribe = self.node.subscribe(lambda changed: changed and self.redraw())

    def on_unmount(self):
        self.unsubscribe()

    def on_resize(self):
        self.redraw()

    def redraw(self):
        grid = self.query_one("#grid", Static)
        grid.update(habit_grid(self.node.store.habit_history(), date.today(), self.size.width - 6, self.back))

    def action_page(self, weeks: int):
        """Show the weeks `weeks` earlier, never past this one."""
        self.back = max(0, self.back + weeks)
        self.redraw()
