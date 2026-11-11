import asyncio
from dataclasses import dataclass

import pytest

from kanso.core.migrations import current_version
from kanso.core.store import Store
from kanso.sync import protocol
from kanso.sync.protocol import (
    CODE_ATTEMPTS,
    CODE_TTL,
    MAX_INTRODUCTIONS,
    Result,
    answer,
    dial,
    introduction,
    new_code,
    normalize,
    proof,
    vouched,
)
from kanso.sync.transport import Channel, MemoryTransport, SyncError


@dataclass
class Node:
    name: str
    store: Store
    network: dict

    @property
    def key(self) -> str:
        return f"key-{self.name}"

    @property
    def transport(self) -> MemoryTransport:
        """On the network, as tests run each session in a new event loop, and queues are per loop."""
        return MemoryTransport(self.key, self.network)


@pytest.fixture
def make(tmp_path):
    """Makes nodes on one in-memory network, each a device of its own network, as on start."""
    network, stores = {}, []

    def make(name: str) -> Node:
        store = Store(tmp_path / f"{name}.db")
        stores.append(store)
        node = Node(name, store, network)
        store.add_device(node.key, name)
        return node

    yield make
    for store in stores:
        store.close()


async def accept_one(node: Node, transport: MemoryTransport):
    key, channel = await transport.accept()
    return await answer(node.store, channel, key, transport, node.name)


def connect(dialer: Node, acceptor: Node, code: str | None = None) -> tuple:
    """Run one session; each side's `Result`, or the `SyncError` it raised."""

    async def main():
        served = asyncio.create_task(accept_one(acceptor, acceptor.transport))
        dialed = dial(dialer.store, dialer.transport, acceptor.key, dialer.name, code)
        return await asyncio.gather(dialed, served, return_exceptions=True)

    return tuple(asyncio.run(main()))


def contents(node: Node) -> list:
    """Lists by name, as lists added on different nodes share position keys and order by id."""
    return sorted((l.name, [t.text for t in node.store.todos(l.id)]) for l in node.store.lists())


def devices(node: Node) -> list[str]:
    return sorted(d.name for d in node.store.devices())


@pytest.fixture
def pair(make):
    """Nodes a and b with a list each, unpaired."""
    a, b = make("a"), make("b")
    a.store.add_todo(a.store.add_list("A").id, "from a")
    b.store.add_todo(b.store.add_list("B").id, "from b")
    return a, b


# Pairing


def test_unpaired_node_is_refused(pair):
    a, b = pair
    dialed, served = connect(a, b)
    assert "Not paired" in str(dialed) and "Not paired" in str(served)
    assert contents(b) == [("B", ["from b"])] and not b.store.is_trusted(a.key)


def test_pairing_joins_the_networks_and_syncs_both_ways(pair):
    a, b = pair
    code = new_code(b.store)
    assert len(code) == 11 and code[5] == "-"
    dialed, served = connect(a, b, f" {code.lower()} ")
    assert dialed == Result("b", changed=4) and served == Result("a", changed=4)  # a list, its todo, a device, its introduction
    assert contents(a) == contents(b) == [("A", ["from a"]), ("B", ["from b"])]
    assert devices(a) == devices(b) == ["a", "b"]
    assert connect(a, b) == (Result("b", 0), Result("a", 0))  # paired: no code needed
    assert connect(b, a) == (Result("a", 0), Result("b", 0))


def test_code_works_once(make, pair):
    a, b = pair
    code = new_code(b.store)
    connect(a, b, code)
    dialed, _ = connect(make("c"), b, code)
    assert "Wrong or expired code" in str(dialed)


def test_too_many_wrong_codes_burn_it(pair):
    a, b = pair
    code = new_code(b.store)
    for _ in range(CODE_ATTEMPTS):
        assert "Wrong" in str(connect(a, b, "WRONG-CODE0")[0])
    assert "Wrong" in str(connect(a, b, code)[0])


def introductions(node: Node) -> set[tuple[str, str]]:
    return {(i.by, i.key) for i in node.store.introductions()}


def test_pairing_is_logged_but_never_the_code_or_the_data(pair, caplog):
    a, b = pair
    a.store.add_todo(a.store.lists()[0].id, "SECRET-TODO")
    code = new_code(b.store)
    connect(a, b, code)
    assert "Made a pairing code, valid 10 minutes" in caplog.messages
    assert "Paired with b (key-b), with its code" in caplog.messages
    assert "Paired with a (key-a), with this device's code" in caplog.messages
    logged = caplog.text  # every record, at every level
    for secret in ("SECRET-TODO", "from a", "from b", code, normalize(code)):
        assert secret not in logged


def test_pairing_introduces_each_to_the_other_on_both_sides(pair):
    a, b = pair
    connect(a, b, new_code(b.store))
    assert introductions(a) == introductions(b) == {(a.key, b.key), (b.key, a.key)}
    assert all(MemoryTransport.verify(i.by, introduction(i.key), i.signature) for i in a.store.introductions())


def test_a_device_introduced_by_a_trusted_one_is_admitted(make, caplog):
    """a paired with b; then b with c while a was away: c reaches a without b."""
    a, b, c = make("a"), make("b"), make("c")
    connect(a, b, new_code(b.store))
    connect(c, b, new_code(b.store))
    c.store.add_todo(c.store.add_list("C").id, "from c")
    dialed, served = connect(c, a)
    assert dialed.peer == "a" and served.peer == "c" and served.changed > 0
    assert a.store.is_trusted(c.key) and contents(a) == [("C", ["from c"])]
    assert "Paired with c (key-c), introduced by devices of this network" in caplog.messages
    assert (a.key, c.key) in introductions(a)  # a vouches for c too now
    assert connect(a, c) == (Result("c", 0), Result("a", 0))


def test_introductions_from_outside_the_network_are_refused(make):
    a, x, y = make("a"), make("x"), make("y")
    connect(x, y, new_code(y.store))
    dialed, served = connect(y, a)
    assert "Not paired" in str(dialed) and "Not paired" in str(served)
    assert not a.store.is_trusted(y.key) and not a.store.knows_device(y.key)


def test_a_removed_device_is_not_admitted_by_introductions(make):
    a, b, c = make("a"), make("b"), make("c")
    connect(a, b, new_code(b.store))
    connect(c, b, new_code(b.store))
    a.store.add_device(c.key, "c")
    removed = a.store.devices()[-1]
    removed.deleted = True
    a.store._save(removed)
    assert "Not paired" in str(connect(c, a)[1]) and not a.store.is_trusted(c.key)


def test_a_code_is_still_checked_when_introductions_would_admit(make):
    a, b, c = make("a"), make("b"), make("c")
    connect(a, b, new_code(b.store))
    connect(c, b, new_code(b.store))
    new_code(a.store)
    assert "Wrong or expired" in str(connect(c, a, "WRONG-CODE0")[1]) and not a.store.is_trusted(c.key)


@pytest.mark.parametrize(
    "given",
    [
        None,
        "not a list",
        [["key-a", "key-c"]],
        [["key-a", "key-c", 5]],
        ["key-a"],
        [["key-a", "key-c", "0" * 64]],  # forged
        [["key-b", "key-c", MemoryTransport("key-b", {}).sign(introduction("key-c"))]],  # by an unknown device
        [["key-a", "key-c", MemoryTransport("key-a", {}).sign(introduction("key-d"))]],  # of another device
    ],
)
def test_bad_introductions_vouch_for_nobody(make, given):
    a = make("a")
    assert not vouched(a.store, a.transport, "key-c", given)


def test_introductions_chain_from_a_trusted_device(make):
    a = make("a")
    sign = lambda by, key: [by, key, MemoryTransport(by, {}).sign(introduction(key))]  # noqa: E731
    chain = [["junk"], sign("key-d", "key-c"), sign("key-b", "key-d"), sign("key-a", "key-b")]
    assert vouched(a.store, a.transport, "key-c", chain)
    assert not vouched(a.store, a.transport, "key-c", chain[:2])


def test_only_so_many_introductions_are_checked(make):
    a = make("a")
    padding = [["key-x", "key-y", "sig"]] * MAX_INTRODUCTIONS
    valid = ["key-a", "key-c", MemoryTransport("key-a", {}).sign(introduction("key-c"))]
    assert vouched(a.store, a.transport, "key-c", [valid, *padding])
    assert not vouched(a.store, a.transport, "key-c", [*padding, valid])


def test_a_miss_keeps_the_code_until_the_limit(pair):
    a, b = pair
    code = new_code(b.store)
    connect(a, b, "WRONG-CODE0")
    assert connect(a, b, code)[0] == Result("b", 4)


def test_expired_code_is_refused(pair, monkeypatch):
    a, b = pair
    code = new_code(b.store)
    later = protocol.time.time() + CODE_TTL + 1
    monkeypatch.setattr(protocol.time, "time", lambda: later)
    assert "Wrong or expired" in str(connect(a, b, code)[0])
    assert b.store.meta("pair_code") is None


def test_new_code_replaces_the_old_one(pair):
    a, b = pair
    old = new_code(b.store)
    new_code(b.store)
    assert "Wrong" in str(connect(a, b, old)[0])


def test_proof_is_bound_to_both_keys_and_ignores_formatting():
    code = "ABCDE-FGHJK"
    assert proof(code, "a", "b") == proof(" abcde fghjk", "a", "b")
    assert len({proof(code, "a", "b"), proof(code, "a", "c"), proof(code, "c", "b"), proof("ABCDE-FGHJM", "a", "b")}) == 4
    assert normalize("ab-cd 12") == "ABCD12"


def test_paired_networks_merge_and_changes_flow_through(make, pair):
    a, b = pair
    c = make("c")
    connect(a, b, new_code(b.store))
    connect(c, b, new_code(b.store))  # c joins through b, learning a's device and data
    assert devices(c) == ["a", "b", "c"] and contents(c) == contents(b)
    connect(a, b)  # a learns c from b
    a.store.add_todo(a.store.lists()[0].id, "late")
    assert connect(c, a)[0] == Result("a", 1)  # c and a trust each other without pairing
    assert contents(c) == contents(a)


# Misbehaving peers


async def scripted(peer: Node, script) -> tuple:
    """Dial `peer` from a node that follows `script(channel)` instead of the protocol."""
    async def main():
        served = asyncio.create_task(accept_one(peer, peer.transport))
        channel = await MemoryTransport("key-x", peer.network).connect(peer.key)
        await script(channel)
        await channel.close()
        return await asyncio.gather(served, return_exceptions=True)

    return await main()


def serve_script(node: Node, *replies) -> tuple:
    """Have `node` dial a peer that answers each of its messages with the next of `replies`."""
    fake = MemoryTransport("key-x", node.network)

    async def peer():
        _, channel = await fake.accept()
        try:
            for reply in replies:
                await channel.receive()
                if reply is None:
                    break
                await channel.send(reply)
        except SyncError:
            pass  # the node gave up first
        await channel.close()

    async def main():
        task = asyncio.create_task(peer())
        try:
            return await dial(node.store, node.transport, "key-x", node.name)
        finally:
            await task

    try:
        return asyncio.run(main())
    except SyncError as error:
        return error


WELCOME = {"type": "welcome", "schema": current_version(), "name": "x"}


def rows_message(**fields) -> dict:
    return {"type": "rows", "schema": current_version(), "digest": {}, "rows": {}, **fields}


def test_peer_with_newer_schema_takes_our_rows_but_we_skip_its(pair):
    a, _ = pair
    newer = {"todolist": [{"id": "z", "future_field": 1}]}
    result = serve_script(a, {**WELCOME, "schema": current_version() + 1}, rows_message(schema=current_version() + 1, rows=newer), None)
    assert result == Result("x", 0, (protocol.PROTOCOL, current_version() + 1))
    assert result.newer and not result.older


# Versions


def test_peers_of_this_version_are_neither_newer_nor_older(pair):
    a, b = pair
    dialed, served = connect(a, b, new_code(b.store))
    assert dialed.version == served.version == protocol.version() == (protocol.PROTOCOL, current_version())
    assert not (dialed.newer or dialed.older or served.newer or served.older)


def test_a_peer_not_sending_its_protocol_speaks_the_first(pair, monkeypatch):
    a, _ = pair
    assert serve_script(a, WELCOME, rows_message(), None) == Result("x", 0, (1, current_version()))
    monkeypatch.setattr(protocol, "PROTOCOL", 2)
    result = serve_script(a, WELCOME, None)  # ends after the welcome
    assert result == Result("x", 0, (1, current_version())) and result.older


def test_an_acceptor_of_a_newer_protocol_ends_the_session_after_welcome(pair):
    a, _ = pair
    before = a.store.digest()
    result = serve_script(a, {**WELCOME, "protocol": protocol.PROTOCOL + 1}, rows_message(rows={"todolist": [{"id": "z"}]}))
    assert result == Result("x", 0, (protocol.PROTOCOL + 1, current_version())) and result.newer
    assert a.store.digest() == before


def test_a_dialer_of_a_newer_protocol_is_welcomed_then_the_session_ends(pair):
    _, b = pair
    b.store.add_device("key-x", "x")
    welcomed = []

    async def newer(channel: Channel):
        await channel.send({"type": "hello", "protocol": protocol.PROTOCOL + 1, "schema": current_version(), "name": "x"})
        welcomed.append(await channel.receive())

    [served] = asyncio.run(scripted(b, newer))
    assert welcomed == [{"type": "welcome", "protocol": protocol.PROTOCOL, "schema": current_version(), "name": "b"}]
    assert served == Result("x", 0, (protocol.PROTOCOL + 1, current_version())) and served.newer


@pytest.mark.parametrize(
    "replies, error",
    [
        ([{"type": "hello"}], "broke the protocol: expected welcome"),
        ([{**WELCOME, "schema": "1"}], "broke the protocol"),
        ([{**WELCOME, "protocol": "2"}], "broke the protocol: protocol 2"),
        ([None], "closed"),
        ([WELCOME, rows_message(rows={"meta": []})], "bad data"),
        ([WELCOME, rows_message(rows={"todolist": [{"id": "z"}]})], "bad data"),
        ([WELCOME, rows_message(digest={"todo": 5})], "bad data"),
    ],
)
def test_misbehaving_acceptor_fails_the_session_without_writes(pair, replies, error):
    a, _ = pair
    before = a.store.digest()
    result = serve_script(a, *replies, None)
    assert isinstance(result, SyncError) and error in str(result)
    assert a.store.digest() == before


def test_misbehaving_dialer_fails_the_session(pair):
    _, b = pair

    async def bad_hello(channel: Channel):
        await channel.send({"type": "digest", "digest": {}})

    [served] = asyncio.run(scripted(b, bad_hello))
    assert isinstance(served, SyncError) and "expected hello" in str(served)


def test_dialer_with_a_valid_code_but_bad_name_is_refused_without_trust(pair):
    _, b = pair
    code = new_code(b.store)

    async def bad_name(channel: Channel):
        await channel.send({"type": "hello", "schema": current_version(), "name": "", "proof": proof(code, "key-x", b.key)})

    [served] = asyncio.run(scripted(b, bad_name))
    assert "bad data" in str(served) and not b.store.is_trusted("key-x")


def test_silent_peer_times_out(pair, monkeypatch):
    a, _ = pair
    monkeypatch.setattr(protocol, "SESSION_TIMEOUT", 0.05)

    async def main():
        MemoryTransport("key-x", a.network)  # accepts nothing, answers nothing
        return await dial(a.store, a.transport, "key-x", "a")

    with pytest.raises(SyncError, match="too long"):
        asyncio.run(main())
