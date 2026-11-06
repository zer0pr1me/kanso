import asyncio
import json
import os
import signal
import subprocess
import sys

import pytest

from conftest import eventually, serving, sync_over
from kanso import control
from kanso.control import NodeClient
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync.node import SyncNode, device_key, owner_lock
from kanso.sync.protocol import new_code
from kanso.sync.transport import Channel, MemoryTransport, StreamPipe, SyncError


@pytest.fixture
def remote(tmp_path):
    """Another device, b, on the network the tests' daemons use."""
    (tmp_path / "b").mkdir()
    store = Store(tmp_path / "b" / "kanso.db")
    yield SyncNode(store, MemoryTransport("key-b", NETWORK), "b")
    store.close()


NETWORK: dict = {}
SPAWN = control.spawn
"""The real one, which the autouse `no_daemon_processes` replaces."""


@pytest.fixture(autouse=True)
def fresh_network():
    NETWORK.clear()


def updates(client: NodeClient) -> list[bool]:
    seen = []
    client.subscribe(seen.append)
    return seen


# The daemon


def test_the_daemon_announces_itself_privately_and_stops_on_request(store, path):
    async def main():
        async with serving(path, NETWORK):
            info = json.loads(control.daemon_file(store).read_text())
            assert set(info) == {"port", "token"}
            if os.name != "nt":
                assert control.daemon_file(store).stat().st_mode & 0o777 == 0o600
            assert await control.running(store)
            assert await control.stop(store)
            assert not control.daemon_file(store).exists() and not await control.running(store)
        assert not await control.stop(store)

    asyncio.run(main())


def test_a_second_daemon_refuses_to_serve(store, path):
    lock = owner_lock(store)

    async def main():
        async with Node(Store(path), sync_over(NETWORK)) as node:
            assert "Another kanso daemon" in await control.serve(node)
            node.store.close()
        assert not control.daemon_file(store).exists()

    asyncio.run(main())
    lock.close()


def test_requests_need_the_token(store, path):
    async def main():
        async with serving(path, NETWORK):
            info = control.daemon_info(store)
            for token in ("wrong", None):
                channel = Channel(StreamPipe(*await asyncio.open_connection(control.HOST, info["port"])))
                await channel.send({"token": token, "op": "status"})
                with pytest.raises(SyncError, match="Connection lost"):
                    await channel.receive()
                await channel.close()

    asyncio.run(main())


def test_unknown_requests_get_no_answer(store, path):
    async def main():
        async with serving(path, NETWORK):
            channel = await control.request(store, "dance")
            with pytest.raises(SyncError):
                await channel.receive()
            await channel.close()

    asyncio.run(main())


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_the_daemon_stops_on_signals(store, path, sig):
    async def main():
        async with Node(Store(path), sync_over(NETWORK)) as node:
            task = asyncio.create_task(control.serve(node))
            await eventually(lambda: control.daemon_info(store))  # written once signals are handled
            os.kill(os.getpid(), sig)
            assert await asyncio.wait_for(task, 1) is None
            assert not control.daemon_file(store).exists()
            node.store.close()

    asyncio.run(main())


@pytest.mark.parametrize("content", [None, "not json", '{"port": "x"}', '{"token": "t"}'])
def test_without_a_readable_daemon_file_none_runs(store, content):
    if content is not None:
        control.daemon_file(store).write_text(content)
    assert control.daemon_info(store) is None
    assert not asyncio.run(control.running(store))


def test_a_stale_daemon_file_means_none_runs(store, path):
    async def main():
        async with serving(path, NETWORK):
            stale = control.daemon_file(store).read_text()
        control.daemon_file(store).write_text(stale)  # as a killed daemon leaves it
        assert not await control.running(store)
        with pytest.raises(SyncError, match="isn't running"):
            await control.request(store, "status")

    asyncio.run(main())


# Clients


def test_a_client_follows_the_daemons_status(store, path):
    async def main():
        async with serving(path, NETWORK) as daemon, NodeClient(store) as client:
            assert client.key == "key-app" and client.sync_note is None and client.online == 0
            seen = updates(client)
            await eventually(lambda: client.synced_at is not None)  # its first round
            daemon.notify(True)
            await eventually(lambda: True in seen)

    asyncio.run(main())


def test_a_client_pairs_through_the_daemon(store, path, remote):
    async def main():
        async with serving(path, NETWORK) as daemon, NodeClient(store) as client:
            task = asyncio.create_task(remote.run())
            with pytest.raises(SyncError, match="Wrong or expired code"):
                await client.pair("key-b", "WRONG-CODE0")
            assert await client.pair("key-b", new_code(remote.store)) == "b"
            assert store.is_trusted("key-b")  # the daemon's merge, seen through this store
            await eventually(lambda: client.online == 1)
            await remote.transport.close()
            await task

    asyncio.run(main())


def test_a_client_without_a_daemon_is_off(store):
    async def main():
        async with NodeClient(store) as client:
            assert "isn't running" in client.sync_note
            assert client.key == device_key(store)
            with pytest.raises(SyncError, match="isn't running"):
                await client.pair("key-b", "CODE")

    asyncio.run(main())


def test_a_client_notices_the_daemon_stopping(store, path):
    async def main():
        async with serving(path, NETWORK), NodeClient(store) as client:
            seen = updates(client)
            await control.stop(store)
            await eventually(lambda: client.sync_note is not None)
            assert "isn't running" in client.sync_note and seen[-1] is False
            assert client.online == 0 and client.key == device_key(store)

    asyncio.run(main())


# Starting the daemon


class Started:
    """Stands in for the daemon's process, serving in this one's loop instead."""

    def __init__(self, path, fail: bool = False):
        self.path, self.fail, self.context, self.calls = path, fail, None, 0

    def spawn(self, store):
        self.calls += 1
        if not self.fail:
            self.context = serving(self.path, NETWORK)
            asyncio.get_running_loop().create_task(self.context.__aenter__())
        return self

    def poll(self):
        return 1 if self.fail else None

    async def close(self):
        if self.context:
            await self.context.__aexit__(None, None, None)


def test_start_spawns_a_daemon_if_none_runs(store, path, monkeypatch):
    started = Started(path)
    monkeypatch.setattr(control, "spawn", started.spawn)

    async def main():
        assert await control.start(store) and started.calls == 1
        assert await control.start(store) and started.calls == 1  # already runs
        assert isinstance(await control.link(store), NodeClient)
        await started.close()

    asyncio.run(main())


def test_without_a_daemon_link_gives_a_node_that_doesnt_sync(store, path, monkeypatch):
    monkeypatch.setattr(control, "spawn", Started(path, fail=True).spawn)
    monkeypatch.setattr(control, "START_TIMEOUT", 0.3)

    async def main():
        assert not await control.start(store)
        node = await control.link(store)
        assert isinstance(node, Node) and node.start_sync is None
        assert "didn't start" in node.sync_note and str(control.log_file(store)) in node.sync_note

    asyncio.run(main())


def test_spawn_runs_the_daemon_detached_on_the_same_data_dir(store, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    SPAWN(store)
    [((command,), options)] = calls
    assert command == [sys.executable, "-m", "kanso", "start", "--foreground"]
    assert options["env"]["KANSO_DATA_DIR"] == str(store.path.parent)
    assert options["stdin"] == subprocess.DEVNULL and options["stderr"] == subprocess.STDOUT
    assert options["start_new_session"] if os.name != "nt" else options["creationflags"]
    assert control.log_file(store).exists()


@pytest.mark.skipif(not os.environ.get("KANSO_NETWORK_TESTS"), reason="needs internet; set KANSO_NETWORK_TESTS=1")
def test_a_real_daemon_starts_serves_and_stops(tmp_path):
    env = {**os.environ, "KANSO_DATA_DIR": str(tmp_path)}
    kanso = [sys.executable, "-m", "kanso"]
    started = subprocess.run([*kanso, "start"], env=env, capture_output=True, text=True, timeout=60)
    assert started.stdout == "Started the kanso daemon\n", started.stderr
    again = subprocess.run([*kanso, "start"], env=env, capture_output=True, text=True, timeout=60)
    assert again.stdout == "The kanso daemon already runs\n"
    stopped = subprocess.run([*kanso, "stop"], env=env, capture_output=True, text=True, timeout=60)
    assert stopped.stdout == "Stopped the kanso daemon\n"
    assert "kanso daemon syncing as" in (tmp_path / "daemon.log").read_text()
