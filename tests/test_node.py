import asyncio
import os

import pytest

from kanso.core.store import Store
from kanso.sync import node as node_module
from kanso.sync import protocol
from kanso.sync.node import SyncNode, owner_lock, start
from kanso.sync.protocol import new_code
from kanso.sync.transport import MemoryTransport, SyncError


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(node_module, "POLL", 0.01)


@pytest.fixture
def make(tmp_path):
    """Makes nodes on one in-memory network; `node.updates` records `on_update` calls."""
    network, stores = {}, []

    def make(name: str) -> SyncNode:
        (tmp_path / name).mkdir()
        store = Store(tmp_path / name / "kanso.db")
        stores.append(store)
        updates = []
        node = SyncNode(store, MemoryTransport(f"key-{name}", network), name, updates.append)
        node.updates = updates
        return node

    yield make
    for store in stores:
        store.close()


async def eventually(condition, timeout: float = 2):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def texts(node: SyncNode) -> list[str]:
    return sorted(t.text for l in node.store.lists() for t in node.store.todos(l.id))


async def running(*nodes: SyncNode) -> list[asyncio.Task]:
    tasks = [asyncio.create_task(n.run()) for n in nodes]
    await eventually(lambda: all(n.store.is_trusted(n.transport.key) for n in nodes))
    return tasks


async def stop(*nodes: SyncNode):
    for n in nodes:
        await n.transport.close()


def add_todo(node: SyncNode, text: str):
    store = node.store
    lists = store.lists() or [store.add_list("Inbox")]
    store.add_todo(lists[0].id, text)


def test_run_registers_own_device_and_ends_when_the_transport_closes(make):
    a = make("a")

    async def main():
        task = asyncio.create_task(a.run())
        await eventually(lambda: [d.name for d in a.store.devices()] == ["a"])
        await stop(a)
        await asyncio.wait_for(task, 1)

    asyncio.run(main())


def test_paired_nodes_send_each_other_their_changes(make):
    a, b = make("a"), make("b")

    async def main():
        await running(a, b)
        result = await a.pair(b.transport.key, new_code(b.store))
        assert result.peer == "b" and a.online == {"key-b"}
        add_todo(a, "from a")
        await eventually(lambda: texts(b) == ["from a"])
        add_todo(b, "from b")
        await eventually(lambda: texts(a) == ["from a", "from b"])
        assert True in a.updates and True in b.updates
        await stop(a, b)

    asyncio.run(main())


def test_changes_reach_devices_through_others(make):
    a, b, c = make("a"), make("b"), make("c")

    async def main():
        await running(a, b, c)
        await a.pair(b.transport.key, new_code(b.store))
        await c.pair(b.transport.key, new_code(b.store))
        add_todo(c, "from c")
        await eventually(lambda: texts(a) == ["from c"])
        await eventually(lambda: a.store.is_trusted("key-c"))  # a learned c's device too
        await stop(a, b, c)

    asyncio.run(main())


def test_a_node_pulls_changes_made_while_it_was_offline(make):
    a, b = make("a"), make("b")

    async def main():
        await running(a, b)
        await a.pair(b.transport.key, new_code(b.store))
        await stop(b)
        add_todo(a, "while b was away")
        await eventually(lambda: a.online == set() and "Can't reach key-b" in (a.error or ""))
        b.transport = MemoryTransport("key-b", a.transport.network)
        await running(b)
        await eventually(lambda: texts(b) == ["while b was away"])
        await stop(a, b)

    asyncio.run(main())


def test_failed_pairing_raises_and_the_node_keeps_serving(make):
    a, b, c = make("a"), make("b"), make("c")

    async def main():
        await running(a, b)
        with pytest.raises(SyncError, match="Wrong or expired"):
            await c.pair(b.transport.key, "WRONG-CODE0")
        with pytest.raises(SyncError, match="Not paired"):
            await protocol.dial(c.store, c.transport, b.transport.key, "c")
        await eventually(lambda: "Not paired" in (b.error or ""))
        await a.pair(b.transport.key, new_code(b.store))
        assert b.store.is_trusted("key-a") and not b.store.is_trusted("key-c")
        await stop(a, b)

    asyncio.run(main())


def test_rounds_repeat_without_changes(make, monkeypatch):
    monkeypatch.setattr(node_module, "ROUND", 0.02)
    a = make("a")

    async def main():
        await running(a)
        await eventually(lambda: a.updates.count(False) >= 3)
        assert a.synced_at is not None
        await stop(a)

    asyncio.run(main())


def test_a_peer_running_newer_kanso_is_reported(make):
    a = make("a")
    a.report(protocol.Result("b", 0, peer_newer=True))
    assert "newer kanso" in a.error


# Owner lock


def test_only_one_process_owns_the_data_dir(tmp_path):
    store = Store(tmp_path / "kanso.db")
    first = owner_lock(store)
    assert first is not None and owner_lock(store) is None
    first.close()
    second = owner_lock(store)
    assert second is not None
    second.close()
    store.close()


# Real network


@pytest.mark.skipif(not os.environ.get("KANSO_NETWORK_TESTS"), reason="needs internet; set KANSO_NETWORK_TESTS=1")
def test_iroh_nodes_pair_and_sync(tmp_path):
    a, b = Store(tmp_path / "a.db"), Store(tmp_path / "b.db")

    async def main():
        nodes = [await start(store, lambda _: None) for store in (a, b)]
        tasks = [asyncio.create_task(n.run()) for n in nodes]
        await nodes[1].transport.endpoint.online()
        a.add_todo(a.add_list("Inbox").id, "over iroh")
        await nodes[0].pair(nodes[1].transport.key, new_code(b))
        await eventually(lambda: [t.text for l in b.lists() for t in b.todos(l.id)] == ["over iroh"], 30)
        key = nodes[0].transport.key
        for n in nodes:
            await n.transport.close()
        await asyncio.gather(*tasks)
        restarted = await start(a, lambda _: None)
        assert restarted.transport.key == key
        await restarted.transport.close()

    asyncio.run(main())
    a.close()
    b.close()
