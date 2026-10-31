from collections.abc import Awaitable, Callable
from datetime import date

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer, Header, OptionList

from kanso.core.models import Repeat
from kanso.core.store import Store
from kanso.sync import node
from kanso.sync.node import SyncNode
from kanso.tui.dialogs import ConnectDialog
from kanso.tui.panes import ListContentsPane, ListsPane, RepeatsPane, TodosPane

type StartNode = Callable[[Store, Callable[[bool], None]], Awaitable[SyncNode]]


class KansoApp(App):
    """Lists on the left; on the right the highlighted list's todos, or its repeats."""

    TITLE = "kanso"
    CSS = """
    #lists { width: 30; }
    #panes { width: 1fr; }
    #todos > .option-list--option-disabled { color: $text-accent; text-style: bold; }
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
            with ContentSwitcher(id="panes", initial="todos"):
                yield TodosPane(self.store, id="todos")
                yield RepeatsPane(self.store, id="repeats")
        yield Footer()

    async def on_mount(self):
        self.store.spawn_repeats(self.day)
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
            pane = self.shown_pane()
            pane.show(pane.current)

    def action_connect(self):
        key = self.node.transport.key if self.node else node.device_key(self.store)
        self.push_screen(ConnectDialog(self.store, key, self.node, self.sync_note))

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
        pane = self.switch_pane("todos" if switcher.current == "repeats" else "repeats")
        pane.show(pane.current)
        if not self.query_one(ListsPane).has_focus:
            self.action_focus_pane()

    def switch_pane(self, pane_id: str) -> ListContentsPane:
        self.query_one("#panes", ContentSwitcher).current = pane_id
        return self.shown_pane()

    def edit_repeat(self, repeat: Repeat):
        """Show the repeats with `repeat` highlighted and edit it."""
        pane = self.switch_pane("repeats")
        pane.show(repeat)
        pane.focus()
        pane.action_edit()


def main():
    store = Store()
    try:
        KansoApp(store, node.start).run()
    finally:
        store.close()


if __name__ == "__main__":
    main()
