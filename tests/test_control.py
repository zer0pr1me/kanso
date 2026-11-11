import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import eventually, serving, sync_over
import kanso
from kanso import __version__, build, code_hash, control
from kanso.control import NodeClient
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync.node import SyncNode, device_key, owner_lock
from kanso.sync.protocol import Result, new_code, version
from kanso.sync.transport import Channel, MemoryTransport, StreamPipe, SyncError


@pytest.fixture
def remote(tmp_path):
    """Another device, b, on the network the tests' daemons use."""
    (tmp_path / "b").mkdir()
    store = Store(tmp_path / "b" / "kanso.db")
    yield SyncNode(store, MemoryTransport("key-b", NETWORK), "b")
    store.close()


NETWORK: dict = {}
SPAWN, IDENTITY = control.spawn, control.identity
"""The real ones: `no_daemon_processes` replaces `spawn`, `daemon_process` `identity`."""


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


def test_a_client_hears_which_devices_need_an_update(store, path):
    async def main():
        async with serving(path, NETWORK) as daemon, NodeClient(store) as client:
            assert not client.outdated and client.behind == []
            store.add_device("key-b", "b")
            p, s = version()
            daemon.sync.report("key-b", Result("b", version=(p, s - 1)))
            await eventually(lambda: client.behind == ["b"])
            daemon.sync.report("key-b", Result("b", version=(p, s + 1)))
            await eventually(lambda: client.outdated and client.behind == [])
            await control.stop(store)
            await eventually(lambda: client.sync_note is not None)
            assert not client.outdated and client.behind == []

    asyncio.run(main())


OLDER = "The kanso daemon runs an older kanso: open kanso again, or run `kanso start`, to run this one"


def test_a_client_of_an_older_daemon_assumes_all_up_to_date_but_the_daemon(store):
    client = NodeClient(store)
    client.update({"key": "key-app", "online": 1, "synced_at": None, "changed": False})
    assert not client.outdated and client.behind == [] and client.online == 1
    assert client.feature_status == {}
    assert client.update_notes() == [OLDER]


def other(**changes) -> dict:
    """The status fields of a daemon running another kanso than this one."""
    return {**control.identity(), "build": "other", **changes}


p, s = version()


@pytest.mark.parametrize(
    "daemon, notes",
    [
        (control.identity(), []),
        (other(), [f"The kanso daemon runs another build of kanso {__version__}: open kanso again, or run `kanso start`, to run this one"]),
        (other(version="0.0.1", sync=[p, s - 1]), ["The kanso daemon runs kanso 0.0.1: open kanso again, or run `kanso start`, to run this one"]),
        (other(version="9.0.0", sync=[p, s + 1]), ["The kanso daemon runs kanso 9.0.0, of a newer sync version: update this kanso"]),
        (other(version="9.0.0", sync=[p + 1, 0]), ["The kanso daemon runs kanso 9.0.0, of a newer sync version: update this kanso"]),
    ],
)
def test_a_client_tells_when_the_daemon_runs_another_kanso(store, daemon, notes):
    client = NodeClient(store)
    client.update({"key": "key-app", "online": 0, "synced_at": None, "changed": False, **daemon})
    assert client.update_notes() == notes
    client.stopped()
    assert client.daemon is None and client.update_notes() == []


def test_a_client_hears_which_kanso_the_daemon_runs(store, path):
    async def main():
        async with serving(path, NETWORK), NodeClient(store) as client:
            assert client.daemon == control.identity() and client.update_notes() == []

    asyncio.run(main())


@pytest.mark.parametrize(
    "daemon, take_over, replaced",
    [
        (control.identity(), False, False),  # the same kanso
        (control.identity(), True, True),
        (other(), False, True),  # a dev build of the same version
        (other(version="0.0.1", sync=[p, s - 1]), False, True),  # older
        (other(version="0.0.1", sync=[p, s]), False, True),  # older, of this sync version
        (other(version="9.0.0", sync=[p, s]), False, True),  # newer, of this sync version
        (other(version="9.0.0", sync=[p, s + 1]), False, False),  # of a newer schema: can't follow it
        (other(version="9.0.0", sync=[p + 1, 0]), True, False),  # of a newer protocol, not even to take over
        ({"key": "key-app"}, False, True),  # before 0.2.3, which told none of it
    ],
)
def test_which_daemons_this_kanso_replaces(daemon, take_over, replaced):
    assert control.replaceable(daemon, take_over) == replaced


def test_the_build_is_a_hash_of_the_code(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.py").write_text("a = 1")
    (tmp_path / "sub" / "b.py").write_text("b = 1")
    first = code_hash(tmp_path)
    (tmp_path / "notes.txt").write_text("not code")
    assert code_hash(tmp_path) == first and len(first) == 16
    (tmp_path / "sub" / "b.py").write_text("b = 2")
    assert code_hash(tmp_path) != first
    (tmp_path / "sub" / "b.py").rename(tmp_path / "b.py")
    (tmp_path / "b.py").write_text("b = 1")
    assert code_hash(tmp_path) != first  # moved
    assert build() == code_hash(Path(kanso.__file__).parent)


@contextlib.asynccontextmanager
async def daemon_process(path, monkeypatch, **identity):
    """A daemon as its own process runs it, until asked to stop, telling it runs `identity`."""
    store = Store(path)
    told = {**IDENTITY(), **identity}
    monkeypatch.setattr(control, "identity", lambda: told)
    task = asyncio.create_task(Node(store, sync_over(NETWORK)).run([control.serve]))
    try:
        await eventually(lambda: control.daemon_info(store))
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        store.close()


def test_start_replaces_a_daemon_running_another_kanso(store, path, monkeypatch, caplog):
    started = Started(path)
    monkeypatch.setattr(control, "spawn", started.spawn)

    async def main():
        async with daemon_process(path, monkeypatch, build="other") as old:
            assert await control.start(store) and started.calls == 1
            assert old.done() and await control.running(store)  # the new one
            await started.close()

    asyncio.run(main())
    started_line = f"The daemon started: kanso {__version__} (build {build()}, sync {p}.{s}), syncing as key-app, in {path.parent}"
    replacing = f"Replacing the daemon running another build of kanso {__version__} with this kanso, {__version__} (build {build()})"
    assert caplog.messages.index(started_line) < caplog.messages.index(replacing)
    assert caplog.messages.index(replacing) < caplog.messages.index("The daemon stops: asked to, by another kanso process")


def test_start_leaves_a_daemon_of_a_newer_sync_version(store, path, monkeypatch, caplog):
    started = Started(path)
    monkeypatch.setattr(control, "spawn", started.spawn)

    async def main():
        async with daemon_process(path, monkeypatch, build="other", sync=[p, s + 1]) as old:
            assert await control.start(store) and started.calls == 0 and not old.done()

    asyncio.run(main())
    assert f"Left the daemon running another build of kanso {__version__}: it speaks a newer sync version" in caplog.messages


def test_a_client_attaches_to_the_daemon_replacing_its_own(store, path, monkeypatch, caplog):
    monkeypatch.setattr(control, "RECONNECT", 0.01)

    async def main():
        async with NodeClient(store) as client:
            assert client.sync_note is not None  # none ran yet
            async with daemon_process(path, monkeypatch, build="other"):
                await eventually(lambda: client.sync_note is None and client.daemon["build"] == "other")
                await control.replace(store)
                await eventually(lambda: client.sync_note is not None)
            async with daemon_process(path, monkeypatch):
                await eventually(lambda: client.sync_note is None and client.update_notes() == [])

    asyncio.run(main())
    assert "Lost the daemon; attaching again once one runs" in caplog.messages
    assert f"Attached to the daemon again: kanso {__version__} (build {build()})" in caplog.messages


class Feature:
    def __init__(self, status=None):
        self.state, self.reloads = status, 0

    def status(self):
        return self.state

    def reload(self):
        self.reloads += 1


def test_a_client_hears_the_configured_features(store, path):
    async def main():
        async with serving(path, NETWORK) as daemon, NodeClient(store) as client:
            assert client.feature_status == {}
            bot = daemon.features["bot"] = Feature()
            daemon.features["idle"] = Feature()  # not configured: not shown
            bot.state = {"title": "Bot", "running": True, "note": "running"}
            daemon.notify(False)
            await eventually(lambda: client.feature_status == {"bot": bot.state})
            await control.stop(store)
            await eventually(lambda: client.sync_note is not None)
            assert client.feature_status == {}

    asyncio.run(main())


def test_reload_reaches_the_feature(store, path):
    async def main():
        async with serving(path, NETWORK) as daemon:
            bot = daemon.features["bot"] = Feature()
            assert await control.ask(store, "reload", feature="bot") == {}
            assert bot.reloads == 1
            assert await control.ask(store, "reload", feature="nope") == {"error": "The kanso daemon has no feature 'nope'"}
            assert await control.ask(store, "reload") == {"error": "The kanso daemon has no feature None"}

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
    assert options["env"]["PYTHONPATH"].split(os.pathsep) == [p for p in sys.path if p]  # this very kanso's code
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
    assert "INFO daemon[" in (text := (tmp_path / "kanso.log").read_text()) and "The daemon started" in text
