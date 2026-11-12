from dataclasses import replace

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Footer, Header, OptionList, Static

from kanso import __version__
from kanso.core.models import Scheduled
from kanso.node import NodeView
from kanso.tui.dialogs import ConnectDialog, HelpDialog
from kanso.tui.panes import HELP_PANES, HabitDaysPane, HabitsPane, ListContentsPane, ListsPane, RepeatsPane, TodosPane, key_rows


class KansoApp(App):
    """Lists on the left; on the right the highlighted list's todos (or repeats) above its
    habit days (or habits). `r` switches the focused side between instances and templates, `F`
    gives the focused side the whole right."""

    TITLE = f"kanso {__version__}"
    CSS = """
    #lists { width: 30; }
    #right { width: 1fr; }
    #top, #bottom { height: 1fr; }
    #right.full-top #bottom, #right.full-bottom #top { display: none; }
    #right.full-top #bottom-title, #right.full-bottom #top-title { color: $text-muted; text-style: italic; }
    .title { width: 1fr; padding: 0 1; text-style: bold; color: $text-accent; background: $accent-muted; }
    #todos > .option-list--option-disabled, #days > .option-list--option-disabled { color: $text-accent; text-style: bold; }
    """

    def __init__(self, node: NodeView):
        """An interface of `node`, entered while the app runs."""
        super().__init__()
        self.node, self.store = node, node.store
        self.unsubscribe = lambda: None
        self.told: set[str] = set()
        """The update notes shown already, so each shows once while it holds."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListsPane(self.store, id="lists")
            with Vertical(id="right"):
                yield Static("TODOS", id="top-title", classes="title")
                with ContentSwitcher(id="top", initial="todos"):
                    yield TodosPane(self.store, id="todos")
                    yield RepeatsPane(self.store, id="repeats")
                yield Static("HABITS", id="bottom-title", classes="title")
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
            if node.outdated:
                self.sub_title += " · update kanso"
            elif node.behind:
                self.sub_title += f" · {len(node.behind)} need{'s' if len(node.behind) == 1 else ''} an update"
        for note in set(node.update_notes()) - self.told:
            self.notify(note, severity="warning", timeout=10)
        self.told = set(node.update_notes())
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

    def key_display(self, binding: Binding) -> str:
        """The keys of `binding`, each as the footer shows it, joined by `/`."""
        keys = binding.key.split(",")
        return binding.key_display or "/".join(self.get_key_display(replace(binding, key=key)) for key in keys)

    def action_help(self):
        """List the keys of every pane, a page each, opening at the focused pane's."""
        pages = [(title, key_rows(pane, self.key_display)) for title, pane in HELP_PANES.items()]
        page = next((i for i, pane in enumerate(HELP_PANES.values()) if isinstance(self.focused, pane)), 0)
        self.push_screen(HelpDialog(pages, page))

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

    def action_fullscreen(self):
        """Give the focused side the whole right, hiding the other but for its title, which
        says so; again restores both."""
        right = self.query_one("#right")
        if right.has_class("full-top") or right.has_class("full-bottom"):
            right.remove_class("full-top", "full-bottom")
        else:
            bottom = self.query_one("#bottom", ContentSwitcher)
            right.add_class("full-bottom" if self.focused in bottom.children else "full-top")
        for side, name in (("top", "TODOS"), ("bottom", "HABITS")):
            hidden = right.has_class(f"full-{'bottom' if side == 'top' else 'top'}")
            self.query_one(f"#{side}-title", Static).update(f"{name} · hidden, F shows" if hidden else name)

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
