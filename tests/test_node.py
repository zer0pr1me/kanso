import asyncio
import os
import signal
from datetime import date, timedelta

import pytest

from kanso import __main__ as cli
from kanso import node as node_module
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync import node as sync_node
from kanso.sync.node import SyncNode, device_key, owner_lock
from kanso.sync.transport import MemoryTransport

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


def sync_over(network: dict, key: str = "key-a"):
    async def start(store, on_update):
        return SyncNode(store, MemoryTransport(key, network), "a", on_update)

    return start


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
            assert node.key == device_key(store)
        lock = owner_lock(store)  # never taken
        assert lock is not None
        lock.close()

    asyncio.run(main())


def test_a_second_process_leaves_sync_to_the_first(store):
    lock = owner_lock(store)

    async def main():
        async with Node(store, sync_over({})) as node:
            assert node.sync is None and "another kanso window" in node.sync_note
            assert node.key == device_key(store)
            store.add_list("Still works")

    asyncio.run(main())
    lock.close()
    assert [l.name for l in store.lists()] == ["Still works"]


def test_syncing_node_owns_the_data_dir_until_stopped(store):
    network = {}

    async def main():
        async with Node(store, sync_over(network)) as node:
            assert node.sync_note is None and node.key == "key-a"
            assert owner_lock(store) is None
            assert "key-a" in network
            async with asyncio.timeout(2):
                while not store.is_trusted("key-a"):  # registered by the running sync node
                    await asyncio.sleep(0.01)
        assert "key-a" not in network and not node.tasks

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


# The executable


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KANSO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sync_node, "start", sync_over({}))
    return tmp_path / "data"


def test_kanso_runs_the_tui_by_default(data_dir, monkeypatch):
    ran = []

    async def tui(node):
        ran.append(node.sync is not None)

    monkeypatch.setattr(cli.tui, "run", tui)
    cli.main([])
    assert ran == [True]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_headless_kanso_syncs_until_stopped(data_dir, capsys, sig):
    async def main():
        async with Node(Store(), sync_over({})) as node:
            task = asyncio.create_task(cli.headless(node))
            while "syncing" not in capsys.readouterr().out:  # printed once the signals are handled
                await asyncio.sleep(0.01)
            assert not task.done()
            os.kill(os.getpid(), sig)
            assert await asyncio.wait_for(task, 1) is None
            node.store.close()

    asyncio.run(main())


def test_headless_kanso_refuses_when_another_process_syncs(data_dir):
    store = Store()
    lock = owner_lock(store)  # held by "another process"; outlives its store
    store.close()
    with pytest.raises(SystemExit, match="Another kanso process already syncs"):
        cli.main(["--no-tui"])
    lock.close()
