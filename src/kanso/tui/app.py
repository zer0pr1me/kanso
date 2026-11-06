from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Footer, Header, OptionList, Static

from kanso.core.models import Scheduled
from kanso.node import NodeView
from kanso.tui.dialogs import ConnectDialog
from kanso.tui.panes import HabitDaysPane, HabitsPane, ListContentsPane, ListsPane, RepeatsPane, TodosPane


class KansoApp(App):
    """Lists on the left; on the right the highlighted list's todos (or repeats) above its
    habit days (or habits). `r` switches the focused side between instances and templates."""

    TITLE = "kanso"
    CSS = """
    #lists { width: 30; }
    #right { width: 1fr; }
    #top, #bottom { height: 1fr; }
    .title { width: 1fr; padding: 0 1; text-style: bold; color: $text-accent; background: $accent-muted; }
    #todos > .option-list--option-disabled, #days > .option-list--option-disabled { color: $text-accent; text-style: bold; }
    """

    def __init__(self, node: NodeView):
        """An interface of `node`, entered while the app runs."""
        super().__init__()
        self.node, self.store = node, node.store
        self.unsubscribe = lambda: None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListsPane(self.store, id="lists")
            with Vertical(id="right"):
                yield Static("TODOS", classes="title")
                with ContentSwitcher(id="top", initial="todos"):
                    yield TodosPane(self.store, id="todos")
                    yield RepeatsPane(self.store, id="repeats")
                yield Static("HABITS", classes="title")
                with ContentSwitcher(id="bottom", initial="days"):
                    yield HabitDaysPane(self.store, id="days")
                    yield HabitsPane(self.store, id="habits")
        yield Footer()

    def on_mount(self):
        self.query_one(ListsPane).show()
        self.unsubscribe = self.node.subscribe(self.node_updated)
        self.node_updated(False)

    def on_unmount(self):
        self.unsubscribe()

    def node_updated(self, changed: bool):
        """Show the sync status; after changes from elsewhere, redraw keeping the highlights."""
        if not self.is_running:  # shutting down: the panes may be gone already
            return
        node, self.sub_title = self.node, ""
        if node.sync_note is None:
            synced = f" · synced {node.synced_at:%H:%M}" if node.synced_at else ""
            self.sub_title = f"{node.online} device{'' if node.online == 1 else 's'} online{synced}"
        if changed:
            lists = self.query_one(ListsPane)
            lists.show(lists.current)
            self.redraw_shown()

    def redraw_shown(self):
        """Reload the panes shown on the right, keeping their highlights."""
        for switcher in ("top", "bottom"):
            pane = self.shown_pane(switcher)
            pane.show(pane.current)

    def action_connect(self):
        self.push_screen(ConnectDialog(self.node))

    def shown_pane(self, switcher_id: str = "top") -> ListContentsPane:
        switcher = self.query_one(f"#{switcher_id}", ContentSwitcher)
        return switcher.get_child_by_id(switcher.current, ListContentsPane)

    @on(OptionList.OptionHighlighted, "#lists")
    @on(ListsPane.Emptied)
    def show_list(self):
        """Show the highlighted list's contents, if any; if it's the same list as before
        (redrawn), keep their highlights."""
        if not self.is_running:  # a redraw's message, arriving while shutting down
            return
        list_id = (current := self.query_one(ListsPane).current) and current.id
        for pane in self.query(ListContentsPane):
            same = pane.list_id == list_id
            pane.list_id = list_id
            pane.show(pane.current if same else None)

    @on(OptionList.OptionSelected, "#lists")
    def enter_list(self):
        self.action_focus_pane()

    def action_focus_pane(self, switcher_id: str = "top"):
        """Focus the side's shown pane, highlighting its first item if none is."""
        pane = self.shown_pane(switcher_id)
        if pane.highlighted is None:
            pane.action_first()
        pane.focus()

    def action_focus_lists(self):
        self.query_one(ListsPane).focus()

    def action_toggle_templates(self):
        """Switch the focused side (the top one from the lists) between instances and their
        templates: todos and repeats, or habit days and habits. Reloads the pane now shown, as
        templates spawn instances, and keeps focus on that side."""
        bottom = self.query_one("#bottom", ContentSwitcher)
        switcher_id = "bottom" if self.focused in bottom.children else "top"
        switcher = bottom if switcher_id == "bottom" else self.query_one("#top", ContentSwitcher)
        instances, templates = [child.id for child in switcher.children]
        pane = self.show_pane(templates if switcher.current == instances else instances)
        pane.show(pane.current)
        if not self.query_one(ListsPane).has_focus:
            self.action_focus_pane(switcher_id)

    def show_pane(self, pane_id: str) -> ListContentsPane:
        """Show the pane `pane_id` on its side."""
        pane = self.query_one(f"#{pane_id}", ListContentsPane)
        pane.parent.current = pane_id
        return pane

    def open_templates(self, pane_id: str, highlight: Scheduled | None = None) -> ListContentsPane:
        """Show and focus the templates pane `pane_id`, highlighting `highlight` if given."""
        pane = self.show_pane(pane_id)
        pane.show(highlight or pane.current)
        pane.focus()
        return pane

    def edit_template(self, pane_id: str, template: Scheduled):
        """Show the templates pane `pane_id` with `template` highlighted and edit it."""
        self.open_templates(pane_id, template).action_edit()

    def add_template(self, pane_id: str):
        """Show the templates pane `pane_id` and add one there."""
        self.open_templates(pane_id).action_add()


async def run(node: NodeView):
    """The TUI as an interface of `node`, until the user quits."""
    await KansoApp(node).run_async()
