import asyncio
import logging
import os

import pytest

from kanso.core.store import Store
from kanso.sync import node as node_module
from kanso.sync import protocol
from kanso.sync.node import SyncNode, owner_lock, start
from kanso.sync.protocol import Result, new_code
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
    return sorted(t.title for l in node.store.lists() for t in node.store.todos(l.id))


async def running(*nodes: SyncNode):
    for n in nodes:
        n.task = asyncio.create_task(n.run())
    await eventually(lambda: all(n.store.is_trusted(n.transport.key) for n in nodes))


async def stop(*nodes: SyncNode):
    """Close their transports and wait until they've stopped."""
    for n in nodes:
        await n.transport.close()
        await asyncio.wait_for(n.task, 1)


def add_todo(node: SyncNode, text: str):
    store = node.store
    lists = store.lists() or [store.add_list("Inbox")]
    store.add_todo(lists[0].id, text)


def test_run_registers_own_device_and_ends_when_the_transport_closes(make):
    a = make("a")

    async def main():
        a.task = asyncio.create_task(a.run())
        await eventually(lambda: [d.name for d in a.store.devices()] == ["a"])
        await stop(a)

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


def restart(*nodes: SyncNode):
    """Give stopped nodes a new transport with their key, as starting kanso again does."""
    for n in nodes:
        n.transport = MemoryTransport(n.transport.key, n.transport.network)


def test_devices_paired_through_an_offline_one_sync(make):
    """a paired with b; while a was away b paired with c, then went away too."""
    a, b, c = make("a"), make("b"), make("c")

    async def main():
        await running(a, b)
        await a.pair(b.transport.key, new_code(b.store))
        await stop(a)
        await running(c)
        await c.pair(b.transport.key, new_code(b.store))
        await stop(b)
        restart(a)
        await running(a)
        add_todo(c, "from c")
        await eventually(lambda: texts(a) == ["from c"])
        assert a.store.is_trusted("key-c")
        add_todo(a, "from a")
        await eventually(lambda: texts(c) == ["from a", "from c"])
        await stop(a, c)

    asyncio.run(main())


def test_introductions_chain_through_several_offline_devices(make):
    """a–b; then b–d and d–c while a was away; only a and c are left online."""
    a, b, c, d = make("a"), make("b"), make("c"), make("d")

    async def main():
        await running(a, b)
        await a.pair(b.transport.key, new_code(b.store))
        await stop(a)
        await running(d)
        await d.pair(b.transport.key, new_code(b.store))
        await stop(b)
        await running(c)
        await c.pair(d.transport.key, new_code(d.store))
        await stop(d)
        restart(a)
        await running(a)
        add_todo(c, "from c")
        await eventually(lambda: texts(a) == ["from c"])
        assert a.store.is_trusted("key-c") and a.store.is_trusted("key-d")
        await stop(a, c)

    asyncio.run(main())


def test_nodes_introduce_devices_they_learned_before_introductions(make):
    a = make("a")
    a.store.add_device("key-b", "b")  # paired by an older kanso, which made no introductions

    async def main():
        await running(a)
        assert [(i.by, i.key) for i in a.store.introductions()] == [("key-a", "key-b")]
        await stop(a)

    asyncio.run(main())


def test_a_device_slow_to_reach_holds_up_no_other(make, monkeypatch):
    a, b, c = make("a"), make("b"), make("c")
    dial = protocol.dial

    async def slow_to_c(store, transport, key, *args):
        if key == "key-c":
            await asyncio.Event().wait()  # as an unreachable device, until the timeout
        return await dial(store, transport, key, *args)

    async def main():
        await running(a, b, c)
        await a.pair(b.transport.key, new_code(b.store))
        await a.pair(c.transport.key, new_code(c.store))
        monkeypatch.setattr(protocol, "dial", slow_to_c)
        await eventually(lambda: "key-c" in a.dialing)
        add_todo(a, "x")
        await eventually(lambda: texts(b) == ["x"])
        assert "key-c" in a.dialing and "key-c" in a.again  # it gets the news once it's through
        await stop(a, b, c)  # also ends the stuck session, for good
        assert a.dialing == {} and a.sessions == set()

    asyncio.run(main())


def test_news_during_a_session_gets_another_once_it_ends(make, monkeypatch):
    a, b = make("a"), make("b")
    dial, gate, dials = protocol.dial, asyncio.Event(), []

    async def held(store, transport, key, *args):
        """a's sessions with b wait for the gate; b may pull from a as it would."""
        if transport.key == "key-a":
            dials.append(key)
            await gate.wait()
        return await dial(store, transport, key, *args)

    async def main():
        await running(a, b)
        await a.pair(b.transport.key, new_code(b.store))
        await eventually(lambda: not a.dialing)
        monkeypatch.setattr(protocol, "dial", held)
        add_todo(a, "x")
        await eventually(lambda: "key-b" in a.dialing)
        add_todo(a, "y")  # after that session began
        await eventually(lambda: "key-b" in a.again)
        gate.set()
        await eventually(lambda: texts(b) == ["x", "y"] and dials.count("key-b") >= 2)
        await stop(a, b)

    asyncio.run(main())


def test_failures_tell_keys_short(make, caplog):
    a = make("a")
    key = "f" * 64
    a.failed(key, SyncError(f"Can't reach {key}: timed out"))
    assert caplog.messages == ["Sync with a device (ffffffffff) failed: Can't reach ffffffffff: timed out"]
    assert a.error == f"Can't reach {key}: timed out"  # what the UI tells stays whole


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


def test_stopping_ends_open_sessions(make):
    a = make("a")

    async def main():
        await running(a)
        silent = MemoryTransport("key-x", a.transport.network)
        await silent.connect("key-a")  # and never says hello
        await eventually(lambda: a.sessions)
        [session] = a.sessions
        await stop(a)
        assert session.cancelled()

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


def test_the_log_tells_when_a_device_comes_and_goes_once(make, monkeypatch, caplog):
    monkeypatch.setattr(node_module, "ROUND", 0.02)
    a, b = make("a"), make("b")

    async def main():
        await running(a, b)
        await a.pair(b.transport.key, new_code(b.store))
        add_todo(a, "x")
        await eventually(lambda: texts(b) == ["x"])
        await stop(b)
        await eventually(lambda: "Can't reach key-b" in a.failures.get("key-b", ()))
        rounds = a.updates.count(False)
        await eventually(lambda: a.updates.count(False) >= rounds + 3)  # more rounds failing alike
        restart(b)
        await running(b)
        await eventually(lambda: "key-b" in a.online)
        await stop(a, b)

    asyncio.run(main())
    of_a = [r.getMessage() for r in caplog.records if r.name == "kanso.sync.node" and r.levelno >= logging.INFO]
    about_b = ["online" if m == "b (key-b) is online" else "failed" for m in caplog.messages if m == "b (key-b) is online" or m.startswith("Sync with b (key-b) failed")]
    changes = [state for i, state in enumerate(about_b) if i == 0 or state != about_b[i - 1]]
    assert about_b.count("online") == changes.count("online") >= 2  # each time it came, never twice in a row
    assert about_b[0] == about_b[-1] == "online"
    unreachable = "Sync with b (key-b) failed: Can't reach key-b"
    failing = [r.levelname for r in caplog.records if r.getMessage() == unreachable]
    assert failing[0] == "WARNING" and len(failing) > 1 and set(failing[1:]) == {"DEBUG"}  # told once, then quietly
    assert not a.failures  # cleared once b was back
    assert any(m.startswith("Synced with b (we dialed): ") for m in of_a)  # the rows pairing brought


def test_devices_on_other_versions_are_reported(make, caplog):
    a = make("a")
    for name in "bcd":
        a.store.add_device(f"key-{name}", name)
    p, s = protocol.version()
    a.report("key-b", Result("b", version=(p, s + 1)))
    a.report("key-c", Result("c", version=(p, s - 1)))
    a.report("key-d", Result("d", version=(p - 1, s + 1)))  # the protocol counts first
    a.report("key-e", Result("e", version=(p, s - 1)))  # not a device of the network
    assert a.outdated and a.behind == ["c", "d"] and a.updates == [False] * 4
    a.report("key-b", Result("b"))  # updated
    assert not a.outdated and a.behind == ["c", "d"]
    told = [m for m in caplog.messages if " runs a kanso of sync version " in m]
    assert told == [f"b runs a kanso of sync version {p}.{s + 1}; this one, {p}.{s}", f"c runs a kanso of sync version {p}.{s - 1}; this one, {p}.{s}",
                    f"d runs a kanso of sync version {p - 1}.{s + 1}; this one, {p}.{s}", f"e runs a kanso of sync version {p}.{s - 1}; this one, {p}.{s}"]
    a.report("key-c", Result("c", version=(p, s - 1)))  # still: not told again
    assert len([m for m in caplog.messages if " runs a kanso of sync version " in m]) == 4


def test_sessions_record_the_peers_version(make):
    a, b = make("a"), make("b")

    async def main():
        await running(a, b)
        await a.pair(b.transport.key, new_code(b.store))
        await eventually(lambda: "key-a" in b.peers)  # b reports once its side of the session ends
        assert a.peers["key-b"].version == b.peers["key-a"].version == protocol.version()
        assert not (a.outdated or a.behind or b.outdated or b.behind)
        await stop(a, b)

    asyncio.run(main())


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


def test_habit_marks_from_another_process_reach_paired_devices(make):
    """A TUI marks through its own store on the daemon's database; the daemon sends it on."""
    from datetime import date, timedelta

    a, b = make("a"), make("b")
    tui = Store(a.store.path)

    async def main():
        await running(a, b)
        await a.pair(b.transport.key, new_code(b.store))
        habit = tui.add_habit(tui.add_list("Inbox").id, "Read")
        habit.start = date.today() - timedelta(days=1)
        tui._save(habit)
        tui.spawn()
        await eventually(lambda: len(b.store.lists()) == 1 and len(b.store.habit_days(habit.list_id)) == 2)
        for state in ("success", "failure", None):
            tui.mark_habit_day(tui.habit_days(habit.list_id)[-1].id, state)
            await eventually(lambda: b.store.habit_days(habit.list_id)[-1].state == state)
        await stop(a, b)

    try:
        asyncio.run(main())
    finally:
        tui.close()
