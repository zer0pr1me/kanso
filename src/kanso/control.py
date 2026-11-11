"""The daemon and how other processes reach it.

The daemon is the data dir's node, running in the background with the features enabled
there. It listens on localhost, writing its port, with a secret token clients must send, to
`daemon.json` in the data dir, readable only by this user. Each connection carries one JSON
request (framed like sync's): `watch` streams the node's status, sent again after every
update; `status`, `pair`, `reload` (a feature, after its config changed) and `stop` answer once.

Other processes, such as the TUI's, share the data through the database and see the node as
a `NodeClient`. `link` starts the daemon if none runs.

A daemon keeps running the code it started with, so the last kanso started runs it, when it
can: `start` replaces a daemon of another build (`kanso.build`), unless that one speaks a
newer sync version (`protocol.version`), which this kanso couldn't follow.
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from kanso import __version__, build
from kanso import log as kanso_log
from kanso.core.store import Store
from kanso.node import Node, NodeView, which_kanso
from kanso.sync.node import device_key, owner_lock
from kanso.sync.protocol import version as sync_version
from kanso.sync.transport import Channel, StreamPipe, SyncError

HOST = "127.0.0.1"
START_TIMEOUT = 20
"""Seconds to wait for a daemon to start listening."""
STOP_TIMEOUT = 10
RECONNECT = 1
"""Seconds between a client's tries to attach to the daemon again, once it went away."""

log = logging.getLogger(__name__)


def daemon_file(store: Store) -> Path:
    return Path(store.path).with_name("daemon.json")


def log_file(store: Store) -> Path:
    return kanso_log.path(Path(store.path).parent)


def status(node: Node, changed: bool = False) -> dict:
    synced_at = node.synced_at and node.synced_at.isoformat()
    return {
        "key": node.key,
        "online": node.online,
        "synced_at": synced_at,
        "outdated": node.outdated,
        "behind": node.behind,
        "features": node.feature_status,
        **identity(),
        "changed": changed,
    }


def identity() -> dict:
    """Which kanso this process runs, as the daemon tells its clients."""
    return {"version": __version__, "build": build(), "sync": list(sync_version())}


def daemon_of(status: dict) -> dict:
    """Which kanso the daemon whose `status` this is runs, as `NodeView.daemon` holds it."""
    return {field: status.get(field) for field in identity()}


def replaceable(daemon: dict, take_over: bool = False) -> bool:
    """Whether this kanso may replace the daemon whose status is `daemon`: if it's of another
    build (or any, to `take_over`), unless it speaks a newer sync version, which this kanso
    couldn't follow. Daemons before 0.2.3 tell neither, and are older."""
    if isinstance(sync := daemon.get("sync"), list) and tuple(sync) > sync_version():
        return False
    return take_over or daemon.get("build") != build()


# The daemon


async def serve(node: Node) -> str | None:
    """The daemon's control interface: serves clients until one asks it to stop, or SIGINT or
    SIGTERM arrives; returns why not if another daemon owns the data dir."""
    if not node.sync:
        log.warning("The daemon didn't start: %s", node.sync_note)
        return node.sync_note
    build()  # of the code as it started, should its files change later
    stop, token, handlers = asyncio.Event(), secrets.token_hex(16), set()
    why = ["SIGINT or SIGTERM"]

    def stopping(reason: str):
        why[0] = reason
        stop.set()

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
                case "reload":
                    if feature := node.features.get(str(request.get("feature"))):
                        feature.reload()
                        await channel.send({})
                    else:
                        await channel.send({"error": f"The kanso daemon has no feature {request.get('feature')!r}"})
                case "stop":
                    await channel.send({})
                    stopping("asked to, by another kanso process")
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
        p, s = sync_version()
        data_dir = Path(node.store.path).parent
        log.info("The daemon started: kanso %s (build %s, sync %d.%d), syncing as %s, in %s", __version__, build(), p, s, node.key, data_dir)
        await stop.wait()
        log.info("The daemon stops: %s", why[0])
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
    return await daemon_status(store) is not None


async def daemon_status(store: Store) -> dict | None:
    """The status of the data dir's daemon, if one runs."""
    try:
        return await ask(store, "status")
    except SyncError:
        return None


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
        if not await self.attach():
            self.stopped()
        self.task = asyncio.create_task(self.keep_watching())
        return self

    async def __aexit__(self, *_):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.watching:
            await self.watching.close()

    async def attach(self) -> bool:
        """Watch the daemon's status; whether one answered."""
        try:
            watching = await request(self.store, "watch")
        except SyncError:
            return False
        try:
            self.update(await watching.receive())
        except SyncError:
            await watching.close()
            return False
        self.watching = watching
        return True

    async def keep_watching(self):
        """Follow the daemon's status; once it goes away, as when another kanso replaces it,
        attach again whenever one runs."""
        while True:
            try:
                while self.watching:
                    self.update(await self.watching.receive())
            except SyncError:
                await self.watching.close()
                self.watching = None
                self.stopped()
                log.info("Lost the daemon; attaching again once one runs")
            await asyncio.sleep(RECONNECT)
            if await self.attach():
                log.info("Attached to the daemon again: kanso %s (build %s)", self.daemon["version"], self.daemon["build"])

    def update(self, message: dict):
        self.key, self.online, self.sync_note = message["key"], message["online"], None
        self.outdated, self.behind = message.get("outdated", False), message.get("behind", [])  # older daemons lack them
        self.feature_status = message.get("features", {})
        self.daemon = daemon_of(message)
        self.synced_at = message["synced_at"] and datetime.fromisoformat(message["synced_at"])
        self.notify(message["changed"])

    def stopped(self):
        self.key = device_key(self.store)
        self.sync_note, self.online = "The kanso daemon isn't running: sync is off", 0
        self.outdated, self.behind, self.feature_status, self.daemon = False, [], {}, None
        self.notify(False)

    async def pair(self, key: str, code: str) -> str:
        reply = await ask(self.store, "pair", key=key, code=code)
        if "error" in reply:
            raise SyncError(reply["error"])
        return reply["peer"]


def command() -> tuple[list[str], dict[str, str]]:
    """How to run this very kanso in another process: its interpreter, and the environment
    to add, putting this process's `sys.path` first, so the child loads the same code. A bare
    `python -m kanso` wouldn't find it on Nix, whose wrapper sets the path inside its script."""
    return [sys.executable, "-m", "kanso"], {"PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}


def spawn(store: Store) -> subprocess.Popen:
    """Start the store's daemon in the background, running this kanso, its output going to
    the log, so it catches even a crash before the daemon sets up its log."""
    detach = (
        {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    kanso, env = command()
    with open(log_file(store), "a") as log:
        return subprocess.Popen(
            [*kanso, "start", "--foreground"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, **env, "KANSO_DATA_DIR": str(Path(store.path).parent)},
            **detach,
        )


async def start(store: Store) -> bool:
    """Make sure the store's daemon runs, starting it if needed, or replacing it with this
    kanso if it's `replaceable`; whether one then runs."""
    if daemon := await daemon_status(store):
        if not replaceable(daemon):
            if daemon.get("build") != build():
                log.info("Left the daemon running %s: it speaks a newer sync version", which_kanso(daemon_of(daemon)))
            return True
        log.info("Replacing the daemon running %s with this kanso, %s (build %s)", which_kanso(daemon_of(daemon)), __version__, build())
        await replace(store)
    log.info("Starting the daemon")
    process = spawn(store)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + START_TIMEOUT
    while loop.time() < deadline:
        if await running(store):
            return True
        if process.poll() is not None:  # failed, or lost a race to another daemon starting
            deadline = min(deadline, loop.time() + 2)
        await asyncio.sleep(0.1)
    log.warning("The daemon didn't start in time")
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


async def replace(store: Store):
    """Stop the store's daemon so that another can take its place: once its process let go
    of the data dir too, which it does just after it stops answering."""
    await stop(store)
    async with asyncio.timeout(STOP_TIMEOUT):
        while not (lock := owner_lock(store)):
            await asyncio.sleep(0.1)
    lock.close()


async def link(store: Store) -> NodeView:
    """The node for an interface in this process: the daemon's, started if none runs; if it
    can't be, a node of this process's own that doesn't sync."""
    if await start(store):
        return NodeClient(store)
    node = Node(store)
    node.sync_note = f"The kanso daemon didn't start, so sync is off: see {log_file(store)}"
    log.warning("Running without the daemon, so without sync")
    return node
