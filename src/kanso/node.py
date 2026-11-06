"""The kanso node: the process every interface runs in.

A node owns the store, syncs it with the other devices when this process owns the data dir,
and spawns each new day's instances. Interfaces (the TUI now; later web, bots, an API) run on
top of it, use only it and its store, and hear about changes made elsewhere via `subscribe`.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date

from kanso.core.store import Store
from kanso.sync import node as sync_node
from kanso.sync.node import SyncNode
from kanso.sync.protocol import Result

type Listener = Callable[[bool], None]
type StartSync = Callable[[Store, Listener], Awaitable[SyncNode]]
type Interface = Callable[["Node"], Awaitable[object]]

DAY_CHECK = 60
"""Seconds between checks whether the date changed."""


class Node:
    def __init__(self, store: Store, start_sync: StartSync | None = None):
        """With `start_sync`, the node syncs, unless another process owns the data dir.
        Use it as an async context manager, or `run` interfaces on it."""
        self.store, self.start_sync = store, start_sync
        self.sync: SyncNode | None = None
        self.sync_note: str | None = "Sync is off"
        """Why the node doesn't sync, if it doesn't."""
        self.day = date.today()
        self.listeners: list[Listener] = []
        self.lock = None
        self.tasks: list[asyncio.Task] = []
        """The sync and day tasks: the sync one ends once its transport closes, the day one is cancelled."""

    @property
    def key(self) -> str:
        """This device's ID, which other devices dial."""
        return self.sync.transport.key if self.sync else sync_node.device_key(self.store)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call `listener(changed)` after sync updates and day changes, `changed` if data
        changed outside this process; returns the function unsubscribing it."""
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def notify(self, changed: bool):
        for listener in list(self.listeners):
            listener(changed)

    def check_day(self):
        """Once the date changes, spawn the new day's todos and habit days."""
        if date.today() == self.day:
            return
        self.day = date.today()
        self.store.spawn(self.day)
        self.notify(True)

    async def keep_day(self):
        while True:
            await asyncio.sleep(DAY_CHECK)
            self.check_day()

    async def pair(self, key: str, code: str) -> Result:
        """Join the network of device `key` with its pairing code; needs sync."""
        return await self.sync.pair(key, code)

    async def __aenter__(self):
        self.day = date.today()
        self.store.spawn(self.day)
        if self.start_sync:
            if lock := sync_node.owner_lock(self.store):
                self.lock, self.sync_note = lock, None
                self.sync = await self.start_sync(self.store, self.notify)
                self.tasks.append(asyncio.create_task(self.sync.run()))
            else:
                self.sync_note = "Sync runs in another kanso window: connect from there"
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
