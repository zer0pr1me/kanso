from collections.abc import Awaitable, Callable
from datetime import date

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Footer, Header, OptionList, Static

from kanso.core.models import Scheduled
from kanso.core.store import Store
from kanso.sync import node
from kanso.sync.node import SyncNode
from kanso.tui.dialogs import ConnectDialog
from kanso.tui.panes import HabitDaysPane, HabitsPane, ListContentsPane, ListsPane, RepeatsPane, TodosPane

type StartNode = Callable[[Store, Callable[[bool], None]], Awaitable[SyncNode]]


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

    def __init__(self, store: Store, start_node: StartNode | None = None):
        """With `start_node`, this process syncs, unless another one owns the data dir."""
        super().__init__()
        self.store = store
        self.day = date.today()
        self.start_node = start_node
        self.node: SyncNode | None = None
        self.lock = None
        self.sync_note: str | None = "Sync is off"

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

    async def on_mount(self):
        self.store.spawn(self.day)
        self.query_one(ListsPane).show()
        self.set_interval(60, self.check_day)
        if self.start_node:
            await self.start_sync()

    async def start_sync(self):
        if not (lock := node.owner_lock(self.store)):
            self.sync_note = "Sync runs in another kanso window: connect from there"
            return
        self.lock, self.sync_note = lock, None
        self.node = await self.start_node(self.store, self.sync_updated)
        self.run_worker(self.node.run(), name="sync", exit_on_error=False)

    async def on_unmount(self):
        if self.node:
            await self.node.transport.close()
        if self.lock:
            self.lock.close()

    def sync_updated(self, changed: bool):
        """Show the sync status; after merges, redraw keeping the highlights."""
        online = len(self.node.online)
        synced = f" · synced {self.node.synced_at:%H:%M}" if self.node.synced_at else ""
        self.sub_title = f"{online} device{'' if online == 1 else 's'} online{synced}"
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
        key = self.node.transport.key if self.node else node.device_key(self.store)
        self.push_screen(ConnectDialog(self.store, key, self.node, self.sync_note))

    def check_day(self):
        """Once the date changes, spawn the new day's todos and habit days, and redraw the dated sections."""
        if date.today() == self.day:
            return
        self.day = date.today()
        self.store.spawn(self.day)
        self.redraw_shown()

    def shown_pane(self, switcher_id: str = "top") -> ListContentsPane:
        switcher = self.query_one(f"#{switcher_id}", ContentSwitcher)
        return switcher.get_child_by_id(switcher.current, ListContentsPane)

    @on(OptionList.OptionHighlighted, "#lists")
    @on(ListsPane.Emptied)
    def show_list(self):
        """Show the highlighted list's contents, if any; if it's the same list as before
        (redrawn), keep their highlights."""
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


def main():
    store = Store()
    try:
        KansoApp(store, node.start).run()
    finally:
        store.close()


if __name__ == "__main__":
    main()
