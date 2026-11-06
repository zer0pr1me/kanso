import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext

import pytest
import textual._wait

from kanso import control
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync.node import SyncNode
from kanso.sync.transport import MemoryTransport


@pytest.fixture(autouse=True)
def quick_idle_checks(monkeypatch):
    """Pilot waits for the app to idle after every key, sleeping 20ms per check; 2ms checks
    find it idle just as well and make the TUI tests several times faster."""
    monkeypatch.setattr(textual._wait, "SLEEP_GRANULARITY", 1 / 500)


@pytest.fixture(autouse=True)
def no_daemon_processes(monkeypatch):
    """Tests never start real daemons, which would outlive them."""

    def spawn(store):
        raise AssertionError("A test tried to start a real daemon")

    monkeypatch.setattr(control, "spawn", spawn)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "kanso.db"


@pytest.fixture
def store(path):
    s = Store(path)
    yield s
    s.close()


@pytest.fixture
def todo_list(store):
    return store.add_list("Inbox")


def texts(store, list_id) -> list[str]:
    return [t.title for t in store.todos(list_id)]


def run(app, check, *contexts):
    """Run `app` headless, inside async `contexts` and on its node if it has one, and await
    `check(app, pilot)` once it has settled."""

    async def main():
        async with AsyncExitStack() as stack:
            for context in contexts:
                await stack.enter_async_context(context)
            async with getattr(app, "node", nullcontext()), app.run_test() as pilot:
                await pilot.pause()
                await check(app, pilot)

    asyncio.run(main())


def notifications(app) -> list[str]:
    return [n.message for n in app._notifications]


async def eventually(condition, timeout: float = 2):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def sync_over(network: dict, key: str = "key-app", name: str = "app"):
    """A `start_sync` for a node on the in-memory `network`, as `key`."""

    async def start(store, on_update):
        return SyncNode(store, MemoryTransport(key, network), name, on_update)

    return start


@asynccontextmanager
async def serving(path, network: dict, key: str = "key-app"):
    """A daemon for the database at `path`, with its own store as its own process would have,
    syncing on `network` as `key`; yields its node."""
    store = Store(path)
    try:
        async with Node(store, sync_over(network, key)) as node:
            task = asyncio.create_task(control.serve(node))
            await eventually(lambda: control.daemon_info(store))
            try:
                yield node
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        store.close()
