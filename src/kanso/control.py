"""The daemon and how other processes reach it.

The daemon is the data dir's node, running in the background with the features enabled
there. It listens on localhost, writing its port, with a secret token clients must send, to
`daemon.json` in the data dir, readable only by this user. Each connection carries one JSON
request (framed like sync's): `watch` streams the node's status, sent again after every
update; `status`, `pair` and `stop` answer once.

Other processes, such as the TUI's, share the data through the database and see the node as
a `NodeClient`. `link` starts the daemon if none runs.
"""

import asyncio
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from kanso.core.store import Store
from kanso.node import Node, NodeView
from kanso.sync.node import device_key
from kanso.sync.transport import Channel, StreamPipe, SyncError

HOST = "127.0.0.1"
START_TIMEOUT = 20
"""Seconds to wait for a daemon to start listening."""
STOP_TIMEOUT = 10


def daemon_file(store: Store) -> Path:
    return Path(store.path).with_name("daemon.json")


def log_file(store: Store) -> Path:
    return Path(store.path).with_name("daemon.log")


def status(node: Node, changed: bool = False) -> dict:
    synced_at = node.synced_at and node.synced_at.isoformat()
    return {"key": node.key, "online": node.online, "synced_at": synced_at, "changed": changed}


# The daemon


async def serve(node: Node) -> str | None:
    """The daemon's control interface: serves clients until one asks it to stop, or SIGINT or
    SIGTERM arrives; returns why not if another daemon owns the data dir."""
    if not node.sync:
        return node.sync_note
    stop, token, handlers = asyncio.Event(), secrets.token_hex(16), set()

    async def handle(reader, writer):
        handlers.add(task := asyncio.current_task())
        channel = Channel(StreamPipe(reader, writer))
        try:
            request = await channel.receive()
            if not hmac.compare_digest(str(request.get("token")), token):
                return
            match request.get("op"):
                case "watch":
                    await watch(node, channel)
                case "status":
                    await channel.send(status(node))
                case "pair":
                    try:
                        await channel.send({"peer": await node.pair(str(request.get("key")), str(request.get("code")))})
                    except SyncError as error:
                        await channel.send({"error": str(error)})
                case "stop":
                    await channel.send({})
                    stop.set()
        except SyncError:
            pass  # the client went away
        finally:
            await channel.close()
            handlers.discard(task)

    server = await asyncio.start_server(handle, HOST, 0)
    signals = [signal.SIGINT, signal.SIGTERM] if os.name != "nt" else []  # there, ctrl+c raises KeyboardInterrupt
    loop = asyncio.get_running_loop()
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    try:
        write_private(daemon_file(node.store), {"port": server.sockets[0].getsockname()[1], "token": token})
        print(f"kanso daemon syncing as {node.key}", flush=True)
        await stop.wait()
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        daemon_file(node.store).unlink(missing_ok=True)
        server.close()
        for task in list(handlers):
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


async def watch(node: Node, channel: Channel):
    updates = asyncio.Queue()
    unsubscribe = node.subscribe(updates.put_nowait)
    try:
        changed = False
        while True:
            await channel.send(status(node, changed))
            changed = await updates.get()
    finally:
        unsubscribe()


def write_private(path: Path, data: dict):
    path.unlink(missing_ok=True)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        json.dump(data, file)


# Clients


def daemon_info(store: Store) -> dict | None:
    try:
        info = json.loads(daemon_file(store).read_text())
        return {"port": int(info["port"]), "token": str(info["token"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


async def request(store: Store, op: str, **fields) -> Channel:
    """A connection to the daemon carrying request `op`; raises `SyncError` if none runs."""
    if not (info := daemon_info(store)):
        raise SyncError("The kanso daemon isn't running")
    try:
        reader, writer = await asyncio.open_connection(HOST, info["port"])
    except OSError:
        raise SyncError("The kanso daemon isn't running") from None
    channel = Channel(StreamPipe(reader, writer))
    await channel.send({"token": info["token"], "op": op, **fields})
    return channel


async def running(store: Store) -> bool:
    """Whether the data dir's daemon runs and answers."""
    try:
        await ask(store, "status")
    except SyncError:
        return False
    return True


async def ask(store: Store, op: str, **fields) -> dict:
    """The daemon's one answer to request `op`."""
    channel = await request(store, op, **fields)
    try:
        return await channel.receive()
    finally:
        await channel.close()


class NodeClient(NodeView):
    """The daemon's node as another process sees it: status and pairing from the daemon,
    data through the store, which shares its database."""

    def __init__(self, store: Store):
        super().__init__(store)
        self.key = ""
        self.watching: Channel | None = None
        self.task: asyncio.Task | None = None

    async def __aenter__(self):
        try:
            self.watching = await request(self.store, "watch")
            self.update(await self.watching.receive())
        except SyncError:
            self.stopped()
            return self
        self.task = asyncio.create_task(self.keep_watching())
        return self

    async def __aexit__(self, *_):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.watching:
            await self.watching.close()

    async def keep_watching(self):
        try:
            while True:
                self.update(await self.watching.receive())
        except SyncError:
            self.stopped()

    def update(self, message: dict):
        self.key, self.online, self.sync_note = message["key"], message["online"], None
        self.synced_at = message["synced_at"] and datetime.fromisoformat(message["synced_at"])
        self.notify(message["changed"])

    def stopped(self):
        self.key = device_key(self.store)
        self.sync_note, self.online = "The kanso daemon isn't running: sync is off", 0
        self.notify(False)

    async def pair(self, key: str, code: str) -> str:
        reply = await ask(self.store, "pair", key=key, code=code)
        if "error" in reply:
            raise SyncError(reply["error"])
        return reply["peer"]


def spawn(store: Store) -> subprocess.Popen:
    """Start the store's daemon in the background, logging to `daemon.log`."""
    detach = (
        {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    with open(log_file(store), "a") as log:
        return subprocess.Popen(
            [sys.executable, "-m", "kanso", "start", "--foreground"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "KANSO_DATA_DIR": str(Path(store.path).parent)},
            **detach,
        )


async def start(store: Store) -> bool:
    """Make sure the store's daemon runs, starting it if needed; whether it then does."""
    if await running(store):
        return True
    process = spawn(store)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + START_TIMEOUT
    while loop.time() < deadline:
        if await running(store):
            return True
        if process.poll() is not None:  # failed, or lost a race to another daemon starting
            deadline = min(deadline, loop.time() + 2)
        await asyncio.sleep(0.1)
    return False


async def stop(store: Store) -> bool:
    """Stop the store's daemon; whether one ran."""
    try:
        await ask(store, "stop")
    except SyncError:
        return False
    async with asyncio.timeout(STOP_TIMEOUT):
        while await running(store):
            await asyncio.sleep(0.1)
    return True


async def link(store: Store) -> NodeView:
    """The node for an interface in this process: the daemon's, started if none runs; if it
    can't be, a node of this process's own that doesn't sync."""
    if await start(store):
        return NodeClient(store)
    node = Node(store)
    node.sync_note = f"The kanso daemon didn't start, so sync is off: see {log_file(store)}"
    return node
