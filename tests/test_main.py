import argparse
import asyncio
import os
import signal
import threading
import time

import tomllib
from pathlib import Path

import pytest

from conftest import eventually, serving, sync_over
from kanso import __main__ as cli
from kanso import __version__, build
from kanso import control
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync import node as sync_node
from kanso.sync.node import SyncNode, device_key, owner_lock
from kanso.core.migrations import current_version
from kanso.sync.protocol import PROTOCOL, new_code
from kanso.sync.protocol import version as sync_version
from kanso.sync.transport import MemoryTransport, SyncError


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KANSO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sync_node, "start", sync_over({}))
    return tmp_path / "data"


def answers(monkeypatch, **results):
    """Make `control`'s async functions named in `results` return them."""
    for name, result in results.items():

        async def answer(store, result=result):
            return result

        monkeypatch.setattr(control, name, answer)


def test_kanso_opens_the_tui_on_the_linked_node(data_dir, monkeypatch):
    ran = []

    async def link(store):
        return Node(store)

    async def tui(node):
        ran.append(node.day is not None and node.store.lists() == [])

    monkeypatch.setattr(control, "link", link)
    monkeypatch.setattr(cli.tui, "run", tui)
    cli.main([])
    assert ran == [True]


p, s = sync_version()


@pytest.mark.parametrize(
    "daemon, output",
    [
        (None, "Started the kanso daemon\n"),
        (control.identity(), "The kanso daemon already runs\n"),
        ({**control.identity(), "version": "0.0.1", "build": "old"}, "Restarted the kanso daemon with this kanso: it ran kanso 0.0.1\n"),
        ({"key": "key-app"}, "Restarted the kanso daemon with this kanso: it ran an older kanso\n"),
        ({**control.identity(), "version": "9.0.0", "build": "new", "sync": [p, s + 1]}, "The kanso daemon already runs\n"),
    ],
)
def test_start(data_dir, monkeypatch, capsys, daemon, output):
    answers(monkeypatch, daemon_status=daemon, start=True)
    cli.main(["start"])
    assert capsys.readouterr().out == output


def test_start_reports_a_daemon_that_didnt_start(data_dir, monkeypatch):
    answers(monkeypatch, daemon_status=None, start=False)
    with pytest.raises(SystemExit, match=f"didn't start: see {data_dir / 'kanso.log'}"):
        cli.main(["start"])


@pytest.mark.parametrize("ran, output", [(True, "Stopped the kanso daemon\n"), (False, "The kanso daemon isn't running\n")])
def test_stop(data_dir, monkeypatch, capsys, ran, output):
    answers(monkeypatch, stop=ran)
    cli.main(["stop"])
    assert capsys.readouterr().out == output


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_the_foreground_daemon_runs_until_sigterm(data_dir, capsys):
    def stop_once_up():
        deadline = time.monotonic() + 5
        while not (data_dir / "daemon.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=stop_once_up).start()
    cli.main(["start", "--foreground"])
    out, err = capsys.readouterr()
    started = f"INFO daemon[{os.getpid()}] kanso.control: The daemon started: kanso {__version__} (build {build()}"
    assert out == "" and started in err and "The daemon stops: SIGINT or SIGTERM" in err
    assert started in (data_dir / "kanso.log").read_text()  # the log, and stderr for systemd's journal
    assert not (data_dir / "daemon.json").exists()


@pytest.mark.parametrize("daemon", [control.identity(), {"key": "key-app"}])
def test_the_foreground_daemon_takes_over_from_a_running_one(data_dir, monkeypatch, daemon):
    done = []
    answers(monkeypatch, daemon_status=daemon)
    monkeypatch.setattr(control, "replace", lambda store: done.append("replaced") or asyncio.sleep(0))

    async def run(self, features):
        done.append("ran")

    monkeypatch.setattr(Node, "run", run)
    cli.main(["start", "--foreground"])
    assert done == ["replaced", "ran"]


def test_the_foreground_daemon_leaves_one_of_a_newer_sync_version(data_dir, monkeypatch, capsys):
    newer = {**control.identity(), "version": "9.0.0", "build": "new", "sync": [p, s + 1]}
    answers(monkeypatch, daemon_status=newer)
    monkeypatch.setattr(Node, "run", lambda self, features: pytest.fail("ran"))
    cli.main(["start", "--foreground"])  # exits fine: systemd shouldn't retry
    assert capsys.readouterr().out == "The kanso daemon runs kanso 9.0.0, of a newer sync version: left it running\n"


def test_the_foreground_daemon_refuses_when_another_runs(data_dir):
    store = Store()
    lock = owner_lock(store)  # held by "another daemon"; outlives its store
    store.close()
    with pytest.raises(SystemExit, match="Another kanso daemon syncs this data directory"):
        cli.main(["start", "--foreground"])
    lock.close()


# Connecting and status, with a daemon serving in the test's loop

NETWORK: dict = {}


@pytest.fixture
def remote(tmp_path):
    """Another device, b, on the network of the tests' daemons."""
    NETWORK.clear()
    (tmp_path / "b").mkdir()
    store = Store(tmp_path / "b" / "kanso.db")
    yield SyncNode(store, MemoryTransport("key-b", NETWORK), "b")
    store.close()


async def command(store, *argv: str) -> str | None:
    args = cli.parser().parse_args(argv)
    return await args.command(store, args)


def with_daemon(path, remote, capsys, check):
    async def main():
        async with serving(path, NETWORK):
            task = asyncio.create_task(remote.run())
            capsys.readouterr()  # the daemon's own line
            try:
                await check()
            finally:
                await remote.transport.close()
                await task

    asyncio.run(main())


def test_connect_shows_this_devices_id_and_a_code_others_pair_with(store, path, remote, capsys):
    async def check():
        assert await command(store, "connect") is None
        own, code, hint = capsys.readouterr().out.splitlines()
        assert own == "This device: key-app" and code.endswith(", valid 10 minutes, once")
        code = code.removeprefix("Code: ").split(",")[0]
        assert hint == f"On the other device, run: kanso connect key-app {code}"
        assert (await remote.pair("key-app", code)).peer == "app"
        await eventually(lambda: store.is_trusted("key-b"))

    with_daemon(path, remote, capsys, check)


def test_connect_pairs_with_another_device_through_the_daemon(store, path, remote, capsys):
    async def check():
        assert await command(store, "connect", "key-b", "WRONG-CODE0") == "Refused: Wrong or expired code"
        assert await command(store, "connect", "key-b", new_code(remote.store)) is None
        assert capsys.readouterr().out == "Connected to b\n" and store.is_trusted("key-b")

    with_daemon(path, remote, capsys, check)


def test_connect_refuses_this_devices_own_id(store, path, remote, capsys):
    async def check():
        assert await command(store, "connect", "key-app", "CODE") == "That's this device's own ID"

    with_daemon(path, remote, capsys, check)


def test_connect_needs_both_id_and_code(store):
    assert asyncio.run(command(store, "connect", "key-b")) == "Give both the other device's ID and its code, or neither"


@pytest.mark.parametrize("argv", [["connect"], ["connect", "key-b", "CODE"]])
def test_connect_reports_a_daemon_that_didnt_start(data_dir, monkeypatch, argv):
    answers(monkeypatch, start=False)
    with pytest.raises(SystemExit, match=f"can't connect: see {data_dir / 'kanso.log'}"):
        cli.main(argv)


def test_status_of_a_running_daemon_counts_the_devices_online(store, path, remote, capsys):
    async def check():
        await command(store, "connect", "key-b", new_code(remote.store))
        capsys.readouterr()
        assert await command(store, "status") is None
        daemon, own, online, synced, log = capsys.readouterr().out.splitlines()
        assert daemon == "Daemon: running, syncing" and own == "This device: key-app"
        assert online == "Devices online: 1 of 1 paired" and synced.startswith("Last synced: 20")
        assert log == f"Log: {path.parent / 'kanso.log'}"

    with_daemon(path, remote, capsys, check)


def test_status_tells_what_needs_an_update(data_dir, monkeypatch, capsys):
    async def ask(store, op):
        return {"key": "key-app", "online": 2, "synced_at": None, "outdated": True, "behind": ["b", "c"], "changed": False, **control.identity()}

    monkeypatch.setattr(control, "ask", ask)
    cli.main(["status"])
    assert capsys.readouterr().out.splitlines()[-4:] == [
        "Last synced: not yet",
        "Update kanso: a device runs a newer one, whose changes this one can't take",
        "b, c need a kanso update to get the changes",
        f"Log: {data_dir / 'kanso.log'}",
    ]


def test_status_without_a_daemon(data_dir, capsys):
    cli.main(["status"])
    store = Store()
    key = device_key(store)
    store.close()
    assert capsys.readouterr().out == (
        "Daemon: not running, so sync is off\n"
        f"This device: {key}\n"
        "Devices online: 0 of 0 paired\n"
        "Last synced: not yet\n"
        f"Log: {data_dir / 'kanso.log'}\n"
    )


# Daemon features


def test_status_tells_how_each_feature_runs(data_dir, monkeypatch, capsys):
    answers(monkeypatch, running=True)

    async def ask(store, op, **fields):
        features = {"web-app": {"title": "Web app", "running": True, "note": "on port 8080"}}
        return {"key": "key-app", "online": 0, "synced_at": None, "changed": False, "features": features}

    monkeypatch.setattr(control, "ask", ask)
    cli.main(["status"])
    assert capsys.readouterr().out.splitlines()[-2] == "Web app: on port 8080"


def test_the_daemon_runs_its_features(data_dir, monkeypatch):
    ran = []

    async def run(self, features):
        ran.append(features)

    monkeypatch.setattr(Node, "run", run)
    cli.main(["start", "--foreground"])
    assert ran == [[control.serve]]


# Help


def help_text(capsys, argv: list[str]) -> str:
    try:
        cli.main(argv)
    except SystemExit as exit:  # argparse's --help
        assert exit.code == 0
    return capsys.readouterr().out


def subcommands(parser) -> argparse._SubParsersAction:
    [commands] = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    return commands


@pytest.mark.parametrize("argv", [["help"], ["--help"], ["-h"]])
def test_help_lists_every_command(data_dir, capsys, argv):
    text = help_text(capsys, argv)
    assert text.startswith("usage: kanso [<command>] [help]")
    assert "opens the terminal UI" in text and "kanso <command> help" in text
    for name in subcommands(cli.parser()).choices:
        assert f"    {name} " in text
    assert not data_dir.exists()  # help opens no store


@pytest.mark.parametrize("name", ["start", "stop", "status", "connect", "version", "help"])
def test_each_command_explains_itself(data_dir, capsys, name):
    forms = [help_text(capsys, argv) for argv in ([name, "help"], ["help", name], [name, "--help"])]
    assert forms[0] == forms[1] == forms[2]
    assert forms[0].startswith(f"usage: kanso {name}")
    assert not data_dir.exists()


def test_help_after_flags_still_explains_the_command(data_dir, capsys):
    assert "--foreground" in help_text(capsys, ["start", "--foreground", "help"])


def test_help_for_an_unknown_command_fails(data_dir, capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main(["help", "nope"])
    assert exit.value.code == 2 and "invalid choice: 'nope'" in capsys.readouterr().err


def test_every_command_and_flag_has_help():
    """Keeps the help complete as commands and flags are added, also commands of commands."""

    def check(parser, prefix):
        commands = subcommands(parser)
        listed = {choice.dest: choice.help for choice in commands._choices_actions}
        for name, sub in commands.choices.items():
            assert listed.get(name), f"`{prefix} {name}` needs a one-line help"
            assert sub.description, f"`{prefix} {name}` needs a description"
            for action in sub._actions:
                if not isinstance(action, argparse._HelpAction):
                    assert action.help, f"`{prefix} {name}`'s {action.dest} needs a help"
                if isinstance(action, argparse._SubParsersAction):
                    check(sub, f"{prefix} {name}")

    check(cli.parser(), "kanso")


# Version


def test_version_is_pyprojects():
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]


@pytest.mark.parametrize("argv", [["version"], ["--version"]])
def test_version_tells_kansos_and_the_sync_version(data_dir, capsys, argv):
    assert help_text(capsys, argv) == f"kanso {__version__} (sync protocol {PROTOCOL}, schema {current_version()})\n"
    assert not data_dir.exists()  # opens no store
