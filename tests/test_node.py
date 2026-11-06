import asyncio
from datetime import date, timedelta

import pytest

from conftest import eventually, sync_over
from kanso import node as node_module
from kanso.node import Node
from kanso.sync.node import device_key, owner_lock
from kanso.sync.protocol import new_code
from kanso.sync.transport import SyncError

TOMORROW = date.today() + timedelta(days=1)


class Tomorrow(date):
    @classmethod
    def today(cls):
        return TOMORROW


@pytest.fixture
def repeating(store):
    todo_list = store.add_list("Inbox")
    store.add_repeat(todo_list.id, "x")
    return todo_list


def days(store, todo_list) -> list[date]:
    return sorted(t.scheduled_at for t in store.todos(todo_list.id))


def test_start_spawns_the_days_instances(store, repeating, monkeypatch):
    monkeypatch.setattr(node_module, "date", Tomorrow)

    async def main():
        async with Node(store):
            assert TOMORROW in days(store, repeating)

    asyncio.run(main())


def test_without_sync_the_node_only_stores(store):
    async def main():
        async with Node(store) as node:
            assert node.sync is None and node.sync_note == "Sync is off"
            assert node.key == device_key(store) and node.online == 0 and node.synced_at is None
        lock = owner_lock(store)  # never taken
        assert lock is not None
        lock.close()

    asyncio.run(main())


def test_a_second_process_leaves_sync_to_the_first(store):
    lock = owner_lock(store)

    async def main():
        async with Node(store, sync_over({})) as node:
            assert node.sync is None and "Another kanso daemon" in node.sync_note
            assert node.key == device_key(store)
            store.add_list("Still works")

    asyncio.run(main())
    lock.close()
    assert [l.name for l in store.lists()] == ["Still works"]


def test_syncing_node_owns_the_data_dir_until_stopped(store):
    network = {}

    async def main():
        async with Node(store, sync_over(network)) as node:
            assert node.sync_note is None and node.key == "key-app"
            assert owner_lock(store) is None
            assert "key-app" in network
            await eventually(lambda: store.is_trusted("key-app"))  # registered by the running sync node
            await eventually(lambda: node.synced_at is not None)
            assert node.online == 0
        assert "key-app" not in network and not node.tasks

    asyncio.run(main())
    lock = owner_lock(store)
    assert lock is not None
    lock.close()


def test_a_new_day_spawns_and_notifies(store, repeating, monkeypatch):
    async def main():
        async with Node(store) as node:
            updates = []
            unsubscribe = node.subscribe(updates.append)
            node.check_day()
            assert updates == [] and TOMORROW not in days(store, repeating)
            monkeypatch.setattr(node_module, "date", Tomorrow)
            node.check_day()
            assert updates == [True] and TOMORROW in days(store, repeating)
            unsubscribe()
            node.notify(False)
            assert updates == [True]

    asyncio.run(main())


def test_the_node_checks_the_day_periodically(store, repeating, monkeypatch):
    monkeypatch.setattr(node_module, "DAY_CHECK", 0.01)

    async def main():
        async with Node(store) as node:
            monkeypatch.setattr(node_module, "date", Tomorrow)
            async with asyncio.timeout(2):
                while node.day != TOMORROW:
                    await asyncio.sleep(0.01)
            assert TOMORROW in days(store, repeating)

    asyncio.run(main())


def test_run_ends_with_the_first_interface_and_stops_the_rest(store):
    stopped = []

    async def quick(node):
        assert node.store is store
        return "done"

    async def forever(node):
        try:
            await asyncio.Future()
        finally:
            stopped.append(True)

    node = Node(store, sync_over({}))
    assert asyncio.run(node.run([forever, quick])) == "done"
    assert stopped == [True] and node.lock is None


def test_an_interface_error_propagates_after_the_node_stops(store):
    async def broken(node):
        raise ValueError("boom")

    node = Node(store, sync_over({}))
    with pytest.raises(ValueError, match="boom"):
        asyncio.run(node.run([broken]))
    assert node.lock is None and not node.tasks



def test_pairing_returns_the_peers_name_or_raises(store, tmp_path):
    network = {}
    (tmp_path / "b").mkdir()
    other = type(store)(tmp_path / "b" / "kanso.db")

    async def main():
        async with Node(store, sync_over(network)) as node, Node(other, sync_over(network, "key-b", "b")):
            with pytest.raises(SyncError, match="Wrong or expired code"):
                await node.pair("key-b", "WRONG-CODE0")
            assert await node.pair("key-b", new_code(other)) == "b"

    asyncio.run(main())
    other.close()
