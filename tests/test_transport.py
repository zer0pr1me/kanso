import asyncio
import os
import struct

import pytest

from kanso.sync.transport import MAX_MESSAGE, Channel, IrohTransport, MemoryPipe, MemoryTransport, SyncError


def run(coro):
    return asyncio.run(coro)


async def pair() -> tuple[str, Channel, Channel]:
    """The dialer's key as the acceptor sees it, and both ends of a channel between two memory nodes."""
    network = {}
    a, b = MemoryTransport("a", network), MemoryTransport("b", network)
    there = await a.connect("b")
    key, back = await b.accept()
    return key, there, back


def test_messages_flow_both_ways_in_order():
    async def main():
        key, there, back = await pair()
        assert key == "a"
        await there.send({"n": 1})
        await there.send({"n": 2, "text": "é" * 100})
        assert [await back.receive(), await back.receive()] == [{"n": 1}, {"n": 2, "text": "é" * 100}]
        await back.send({"ok": True})
        assert await there.receive() == {"ok": True}

    run(main())


def test_receiving_after_peer_closed_fails():
    async def main():
        _, there, back = await pair()
        await there.send({"last": True})
        await there.close()
        assert await back.receive() == {"last": True}
        with pytest.raises(SyncError, match="closed"):
            await back.receive()

    run(main())


def raw_channel() -> tuple[Channel, asyncio.Queue]:
    """A channel reading what the test puts on the returned queue."""
    incoming = asyncio.Queue()
    return Channel(MemoryPipe(incoming, asyncio.Queue())), incoming


@pytest.mark.parametrize(
    "frame, error",
    [
        (struct.pack(">I", MAX_MESSAGE + 1), "over the"),
        (struct.pack(">I", 3) + b"{x}", "malformed"),
        (struct.pack(">I", 2) + b"[]", "isn't an object"),
    ],
)
def test_bad_frames_are_refused(frame, error):
    async def main():
        channel, incoming = raw_channel()
        await incoming.put(frame)
        with pytest.raises(SyncError, match=error):
            await channel.receive()

    run(main())


def test_oversized_message_is_not_sent(monkeypatch):
    monkeypatch.setattr("kanso.sync.transport.MAX_MESSAGE", 10)

    async def main():
        _, there, _ = await pair()
        with pytest.raises(SyncError, match="over the"):
            await there.send({"text": "x" * 10})

    run(main())


def test_unknown_or_closed_peer_is_unreachable():
    async def main():
        network = {}
        a, b = MemoryTransport("a", network), MemoryTransport("b", network)
        await b.close()
        for key in ("b", "nobody"):
            with pytest.raises(SyncError, match="Can't reach"):
                await a.connect(key)
        with pytest.raises(SyncError, match="closed"):
            await b.accept()

    run(main())


network = pytest.mark.skipif(not os.environ.get("KANSO_NETWORK_TESTS"), reason="needs internet; set KANSO_NETWORK_TESTS=1")


@network
def test_iroh_nodes_connect_by_key_alone():
    async def main():
        key = IrohTransport.new_secret_key()
        a, b = await IrohTransport.start(key), await IrohTransport.start(IrohTransport.new_secret_key())
        try:
            await a.endpoint.online()  # published its address, so it can be dialed by key
            accepted = asyncio.create_task(a.accept())
            there = await b.connect(a.key)
            await there.send({"hello": "a"})
            dialer, back = await accepted
            assert dialer == b.key
            assert await back.receive() == {"hello": "a"}
            await back.send({"hello": "b"})
            assert await there.receive() == {"hello": "b"}
            await asyncio.gather(there.close(), back.close())
            with pytest.raises(SyncError, match="Can't reach"):
                await b.connect("not a key")
        finally:
            await a.close()
            await b.close()
        restarted = await IrohTransport.start(key)
        assert restarted.key == a.key
        await restarted.close()

    run(main())
