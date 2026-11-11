"""The kanso node, and the view of it interfaces get.

A node owns the store, syncs it with the other devices when its process owns the data dir,
and spawns each new day's instances. It runs in the daemon (`control.serve`), alongside the
features enabled there. Interfaces see it as a `NodeView`: the node itself in the daemon, or
a `control.NodeClient` in another process, such as the TUI's. Either way they use only the
view and its store, and hear about changes made elsewhere via `subscribe`.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Protocol

from kanso import __version__, build
from kanso.core.store import Store
from kanso.sync import node as sync_node
from kanso.sync.node import SyncNode
from kanso.sync.protocol import version as sync_version

type Listener = Callable[[bool], None]
type StartSync = Callable[[Store, Listener], Awaitable[SyncNode]]
type Interface = Callable[["Node"], Awaitable[object]]

def which_kanso(daemon: dict) -> str:
    """Which kanso `daemon` (`NodeView.daemon`) runs, as a note tells it."""
    if not (version := daemon["version"]):
        return "an older kanso"
    return f"another build of kanso {version}" if version == __version__ else f"kanso {version}"


log = logging.getLogger(__name__)

DAY_CHECK = 60
"""Seconds between checks whether the date changed."""


class Feature(Protocol):
    """A feature running in the daemon, registered in `Node.features` by name."""

    def status(self) -> dict | None:
        """`{"title", "running", "note"}` for interfaces to show; `None` if it isn't configured."""

    def reload(self):
        """Re-read its config, as it changed."""


class NodeView:
    """The node as interfaces see it; an async context manager, entered while they run."""

    store: Store
    key: str
    """This device's ID, which other devices dial."""
    sync_note: str | None = "Sync is off"
    """Why the node doesn't sync, if it doesn't; then it can't pair either."""
    online = 0
    """How many other devices the node reached in its last round."""
    synced_at: datetime | None = None
    outdated = False
    """Whether another device runs a newer kanso, so this one can't take its changes."""
    behind: list[str] = []
    """The names of the devices running an older kanso, which can't take this one's changes."""
    feature_status: dict[str, dict] = {}
    """The status of each configured daemon feature, by name; see `Feature.status`."""
    daemon: dict | None = None
    """Which kanso the daemon this view attaches to runs, if that's another process: its
    `version`, `build` and `sync` version, each `None` from a daemon too old to tell
    (`control.identity`). A daemon keeps running the kanso it started with."""

    def __init__(self, store: Store):
        self.store = store
        self.listeners: list[Listener] = []

    def update_notes(self) -> list[str]:
        """What needs a kanso update, for interfaces to tell the user."""
        notes = ["Update kanso: a device runs a newer one, whose changes this one can't take"] if self.outdated else []
        if behind := self.behind:
            notes.append(f"{', '.join(behind)} {'needs' if len(behind) == 1 else 'need'} a kanso update to get the changes")
        if (daemon := self.daemon) is not None and daemon["build"] != build():
            if isinstance(daemon["sync"], list) and tuple(daemon["sync"]) > sync_version():
                notes.append(f"The kanso daemon runs {which_kanso(daemon)}, of a newer sync version: update this kanso")
            else:
                notes.append(f"The kanso daemon runs {which_kanso(daemon)}: open kanso again, or run `kanso start`, to run this one")
        return notes

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call `listener(changed)` after sync updates and day changes, `changed` if data
        changed outside this process; returns the function unsubscribing it."""
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def notify(self, changed: bool):
        for listener in list(self.listeners):
            listener(changed)

    async def pair(self, key: str, code: str) -> str:
        """Join the network of device `key` with its pairing code; returns its name. Raises
        `SyncError` if that fails."""
        raise NotImplementedError

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class Node(NodeView):
    def __init__(self, store: Store, start_sync: StartSync | None = None):
        """With `start_sync`, the node syncs, unless another process owns the data dir.
        Use it as an async context manager, or `run` interfaces on it."""
        super().__init__(store)
        self.start_sync = start_sync
        self.sync: SyncNode | None = None
        self.day = date.today()
        self.lock = None
        self.tasks: list[asyncio.Task] = []
        """The sync and day tasks: the sync one ends once its transport closes, the day one is cancelled."""
        self.features: dict[str, Feature] = {}
        """The daemon features running on this node, which register themselves here."""

    @property
    def key(self) -> str:
        return self.sync.transport.key if self.sync else sync_node.device_key(self.store)

    @property
    def online(self) -> int:
        return len(self.sync.online) if self.sync else 0

    @property
    def synced_at(self) -> datetime | None:
        return self.sync and self.sync.synced_at

    @property
    def outdated(self) -> bool:
        return bool(self.sync and self.sync.outdated)

    @property
    def behind(self) -> list[str]:
        return self.sync.behind if self.sync else []

    @property
    def feature_status(self) -> dict[str, dict]:
        return {name: status for name, feature in self.features.items() if (status := feature.status())}

    def check_day(self):
        """Once the date changes, spawn the new day's todos and habit days."""
        if date.today() == self.day:
            return
        self.day = date.today()
        spawned = self.store.spawn(self.day)
        log.info("A new day, %s: spawned %d todos and habit days", self.day, len(spawned))
        self.notify(True)

    async def keep_day(self):
        while True:
            await asyncio.sleep(DAY_CHECK)
            self.check_day()

    async def pair(self, key: str, code: str) -> str:
        return (await self.sync.pair(key, code)).peer

    async def __aenter__(self):
        self.day = date.today()
        self.store.spawn(self.day)
        if self.start_sync:
            if lock := sync_node.owner_lock(self.store):
                self.lock, self.sync_note = lock, None
                self.sync = await self.start_sync(self.store, self.notify)
                self.tasks.append(asyncio.create_task(self.sync.run()))
            else:
                self.sync_note = "Another kanso daemon syncs this data directory"
        self.tasks.append(asyncio.create_task(self.keep_day()))
        return self

    async def __aexit__(self, *_):
        if self.sync:
            await self.sync.transport.close()
        self.tasks[-1].cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        if self.lock:
            self.lock.close()
            self.lock = None

    async def run(self, interfaces: list[Interface]):
        """Run the node with `interfaces` until the first of them returns, stopping the others;
        returns its result, or raises its error."""
        async with self:
            tasks = [asyncio.create_task(interface(self)) for interface in interfaces]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            return done.pop().result()
