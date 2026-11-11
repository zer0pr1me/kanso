"""A running sync node: serves peers, and keeps syncing with every device of its network.

Only one process per data dir may be the node (`owner_lock`); other kanso processes on the
same database just read and write it, and the node picks their changes up by polling.
"""

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from kanso.core.store import Store
from kanso.log import short
from kanso.sync import protocol
from kanso.sync.protocol import Result, version
from kanso.sync.transport import IrohTransport, SyncError, Transport

POLL = 2
"""Seconds between checks for local changes to send."""
ROUND = 60
"""Seconds between syncs with every device even without local changes, to pull theirs."""

log = logging.getLogger(__name__)


class SyncNode:
    def __init__(self, store: Store, transport: Transport, name: str, on_update: Callable[[bool], None] = lambda _: None):
        """`on_update(changed)` runs after each session and round; `changed` if rows were merged."""
        self.store, self.transport, self.name = store, transport, name
        self.on_update = on_update
        self.online: set[str] = set()
        """Keys of the devices reached in the last round."""
        self.synced_at: datetime | None = None
        self.error: str | None = None
        """Why the last session failed, if it did."""
        self.peers: dict[str, Result] = {}
        """The last session's result with each device, by key: its name and version."""
        self.failures: dict[str, set[str]] = {}
        """How sessions with each device failed, by key, since one last went through."""
        self.synced_change: str | None = None
        self.wake = asyncio.Event()
        self.sessions: set[asyncio.Task] = set()
        """Every session going, served or dialed."""
        self.dialing: dict[str, asyncio.Task] = {}
        """The session `sync_all` started with each device, by key, while it's going."""
        self.again: set[str] = set()
        """Devices to dial again once their session ends, as there's news since it began."""

    async def run(self):
        """Serve and sync until the transport closes; returns once no session is left running."""
        self.store.add_device(self.transport.key, self.name)
        protocol.introduce_all(self.store, self.transport)
        syncing = asyncio.create_task(self.keep_synced())
        try:
            await self.serve()
        finally:
            tasks = [syncing, *self.sessions]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def serve(self):
        try:
            while True:
                key, channel = await self.transport.accept()
                session = asyncio.create_task(self.answer(key, channel))
                self.sessions.add(session)
                session.add_done_callback(self.sessions.discard)
        except SyncError:
            pass  # closed

    async def answer(self, key: str, channel):
        try:
            result = await protocol.answer(self.store, channel, key, self.transport, self.name)
        except SyncError as error:
            self.failed(key, error)
            return
        self.report(key, result, "dialed us")

    async def keep_synced(self):
        """Sync with every device on start, after local changes, and every `ROUND` seconds."""
        loop = asyncio.get_running_loop()
        next_round = 0.0
        while True:
            if self.store.last_change() != self.synced_change or loop.time() >= next_round:
                next_round = loop.time() + ROUND
                self.sync_all()
            try:
                await asyncio.wait_for(self.wake.wait(), POLL)
            except TimeoutError:
                pass
            self.wake.clear()

    def sync_all(self):
        """Start a session with each other device of the network, without waiting for them, so a
        device slow to reach holds up none of the others. One whose last session is still going
        gets another once it ends, which carries what changed since."""
        protocol.introduce_all(self.store, self.transport)
        self.synced_change = self.store.last_change()
        for key in [d.key for d in self.store.devices() if d.key != self.transport.key]:
            if key in self.dialing:
                self.again.add(key)
            else:
                self.start_session(key)
        self.synced_at = datetime.now()
        self.on_update(False)

    def start_session(self, key: str):
        task = asyncio.create_task(self.dial_in_round(key))
        self.dialing[key] = task
        self.sessions.add(task)
        task.add_done_callback(lambda done: self.session_ended(key, done))

    async def dial_in_round(self, key: str):
        try:
            await self.dial(key)
        except SyncError:
            pass  # noted by `failed`

    def session_ended(self, key: str, task: asyncio.Task):
        self.sessions.discard(task)
        del self.dialing[key]
        if key in self.again:
            self.again.discard(key)
            if not task.cancelled():  # not stopping
                self.start_session(key)

    async def dial(self, key: str, code: str | None = None) -> Result:
        try:
            result = await protocol.dial(self.store, self.transport, key, self.name, code)
        except SyncError as error:
            self.failed(key, error)
            raise
        self.report(key, result, "we dialed")
        return result

    def report(self, key: str, result: Result, how: str = "reported"):
        """Note a session with device `key` that went through; `how` it began, for the log."""
        if key not in self.online:
            log.info("%s (%s) is online", result.peer, short(key))
        previous = self.peers.get(key)
        if result.version != version() and (previous is None or previous.version != result.version):
            log.info("%s runs a kanso of sync version %s.%s; this one, %s.%s", result.peer, *result.version, *version())
        self.error, self.peers[key] = None, result
        self.online.add(key)
        self.failures.pop(key, None)
        log.log(logging.INFO if result.changed else logging.DEBUG, "Synced with %s (%s): %d rows in", result.peer, how, result.changed)
        self.on_update(result.changed > 0)

    def failed(self, key: str, error: SyncError):
        """Note a session with `key` that failed: in the log once for each way it fails, until
        a session goes through again."""
        self.error = str(error)
        self.online.discard(key)
        seen = self.failures.setdefault(key, set())
        level = logging.DEBUG if self.error in seen else logging.WARNING
        seen.add(self.error)
        device = next((d.name for d in self.store.devices() if d.key == key), "a device")
        log.log(level, "Sync with %s (%s) failed: %s", device, short(key), self.error.replace(key, short(key)))
        self.on_update(False)

    @property
    def outdated(self) -> bool:
        """Whether a device runs a newer kanso, so this one can't take its changes."""
        return any(result.newer for result in self.trusted_peers())

    @property
    def behind(self) -> list[str]:
        """The names of the devices running an older kanso, which can't take this one's changes."""
        return sorted(result.peer for result in self.trusted_peers() if result.older)

    def trusted_peers(self) -> list[Result]:
        return [result for key, result in self.peers.items() if self.store.is_trusted(key)]

    async def pair(self, key: str, code: str) -> Result:
        """Join the network of node `key` with its pairing code; then sync with all its devices."""
        result = await self.dial(key, code)
        self.wake.set()
        return result


def owner_lock(store: Store):
    """An open lock file making this process the data dir's sync node, or `None` if another
    process is; the lock lasts while the file stays open."""
    file = open(Path(store.path).with_name("sync.lock"), "a")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        return None
    return file


def secret_key(store: Store) -> bytes:
    """This replica's secret key on the network, made the first time."""
    return bytes.fromhex(store.meta("secret_key", lambda: IrohTransport.new_secret_key().hex()))


def device_key(store: Store) -> str:
    """This replica's public key: what other devices dial."""
    return IrohTransport.public_key(secret_key(store))


async def start(store: Store, on_update: Callable[[bool], None]) -> SyncNode:
    """A node on the iroh network, with this replica's key."""
    transport = await IrohTransport.start(secret_key(store))
    return SyncNode(store, transport, socket.gethostname() or "kanso", on_update)
