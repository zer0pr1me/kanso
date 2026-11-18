"""The statistics screens: of habits, and of repeating todos."""

from datetime import date

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from kanso.node import NodeView
from kanso.tui.format import HABIT_NAMES, REPEAT_NAMES, stats_grid
from kanso.tui.widgets import with_layouts


class Stats(Screen):
    """Every list's templates' marks over the last weeks, with their streaks and success rates
    (`format.stats_grid`); `h`/`l` page a week earlier or later, `?` opens the help.
    Redraws on changes from elsewhere and on resizing."""

    BINDINGS = with_layouts([
        Binding("h,left", "page(1)", "Earlier", key_display="h/l"),
        Binding("l,right", "page(-1)", "Later", show=False),
        Binding("escape,q,s", "app.pop_screen", "Back"),
    ]) + [Binding("question_mark", "app.help", "Help", key_display="?")]
    CSS = "Stats #grid { padding: 1 2; }"
    heading = names = empty = ""

    def __init__(self, node: NodeView):
        super().__init__()
        self.node, self.back = node, 0
        self.unsubscribe = lambda: None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.heading, classes="title")
        with VerticalScroll():
            yield Static(id="grid")
        yield Footer()

    def on_mount(self):
        self.unsubscribe = self.node.subscribe(lambda changed: changed and self.redraw())

    def on_unmount(self):
        self.unsubscribe()

    def on_resize(self):
        self.redraw()

    def history(self) -> list:
        raise NotImplementedError

    def redraw(self):
        grid = stats_grid(self.history(), date.today(), self.size.width - 6, self.back, self.names, self.empty)
        self.query_one("#grid", Static).update(grid)

    def action_page(self, weeks: int):
        """Show the weeks `weeks` earlier, never past this one."""
        self.back = max(0, self.back + weeks)
        self.redraw()


class HabitStats(Stats):
    heading, names, empty = "HABIT STATS", HABIT_NAMES, "No habits yet"

    def history(self) -> list:
        return self.node.store.habit_history()


class RepeatStats(Stats):
    """A repeating todo's day is a success once it's done, a failure if it wasn't by its date."""

    heading, names, empty = "REPEATING TODO STATS", REPEAT_NAMES, "No repeating todos yet"

    def history(self) -> list:
        return self.node.store.repeat_history()
