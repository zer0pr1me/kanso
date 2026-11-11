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
from kanso import __version__
from kanso import control
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync import node as sync_node
from kanso.sync.node import SyncNode, device_key, owner_lock
from kanso.core.migrations import current_version
from kanso.sync.protocol import PROTOCOL, new_code
from kanso.sync.transport import MemoryTransport, SyncError
from kanso.telegram import bot as telegram


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


@pytest.mark.parametrize(
    "running, started, output",
    [(True, None, "The kanso daemon already runs\n"), (False, True, "Started the kanso daemon\n")],
)
def test_start(data_dir, monkeypatch, capsys, running, started, output):
    answers(monkeypatch, running=running, start=started)
    cli.main(["start"])
    assert capsys.readouterr().out == output


def test_start_reports_a_daemon_that_didnt_start(data_dir, monkeypatch):
    answers(monkeypatch, running=False, start=False)
    with pytest.raises(SystemExit, match=f"didn't start: see {data_dir / 'daemon.log'}"):
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
    assert capsys.readouterr().out == "kanso daemon syncing as key-app\n"
    assert not (data_dir / "daemon.json").exists()


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
    with pytest.raises(SystemExit, match=f"can't connect: see {data_dir / 'daemon.log'}"):
        cli.main(argv)


def test_status_of_a_running_daemon_counts_the_devices_online(store, path, remote, capsys):
    async def check():
        await command(store, "connect", "key-b", new_code(remote.store))
        capsys.readouterr()
        assert await command(store, "status") is None
        daemon, own, online, synced = capsys.readouterr().out.splitlines()
        assert daemon == "Daemon: running, syncing" and own == "This device: key-app"
        assert online == "Devices online: 1 of 1 paired" and synced.startswith("Last synced: 20")

    with_daemon(path, remote, capsys, check)


def test_status_tells_what_needs_an_update(data_dir, monkeypatch, capsys):
    async def ask(store, op):
        return {"key": "key-app", "online": 2, "synced_at": None, "outdated": True, "behind": ["b", "c"], "changed": False}

    monkeypatch.setattr(control, "ask", ask)
    cli.main(["status"])
    assert capsys.readouterr().out.splitlines()[-3:] == [
        "Last synced: not yet",
        "Update kanso: a device runs a newer one, whose changes this one can't take",
        "b, c need a kanso update to get the changes",
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
    )


# The Telegram bot

TOKEN = "123:abc-DEF_9"


def running_bot(note="running as @kanso_bot", running=True):
    return {"title": "Telegram bot", "running": running, "note": note}


def daemon(monkeypatch, *bots, reload=None):
    """A daemon that answers status with each of `bots` in turn (then the last), and reload
    with `reload` (raising it if it's an error); returns the ops it was asked."""
    asked, states = [], list(bots)
    answers(monkeypatch, start=True, running=True)

    async def ask(store, op, **fields):
        asked.append((op, fields))
        if op == "reload":
            if isinstance(reload, Exception):
                raise reload
            return reload or {}
        bot = states.pop(0) if len(states) > 1 else states[0] if states else None
        return {"key": "key-app", "online": 0, "synced_at": None, "changed": False, "features": {"telegram-bot": bot} if bot else {}}

    monkeypatch.setattr(control, "ask", ask)
    return asked


def bot_config(data_dir):
    store = Store()
    try:
        return telegram.config(store)
    finally:
        store.close()


def test_starting_the_bot_saves_it_reloads_it_and_tells_how_it_runs(data_dir, monkeypatch, capsys):
    asked = daemon(monkeypatch, running_bot(telegram.STARTING, False), running_bot())
    sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: sleep(0))
    cli.main(["telegram-bot", "start", TOKEN, "-42", "--at", "7:05"])
    assert capsys.readouterr().out == "Telegram bot: running as @kanso_bot\n"
    assert bot_config(data_dir) == {"token": TOKEN, "chat": -42, "at": "07:05"}
    assert asked[0] == ("reload", {"feature": "telegram-bot"})
    cli.main(["telegram-bot", "start", TOKEN, "42"])
    assert bot_config(data_dir)["at"] == "09:00"


def test_a_bot_that_fails_to_start_says_why(data_dir, monkeypatch):
    daemon(monkeypatch, running_bot("Telegram refused the token: check it with @BotFather", False))
    with pytest.raises(SystemExit, match="^Telegram bot: Telegram refused the token"):
        cli.main(["telegram-bot", "start", TOKEN, "42"])
    assert bot_config(data_dir) is not None  # kept: `kanso status` keeps telling


def test_a_bot_still_starting_is_left_to_status(data_dir, monkeypatch, capsys):
    daemon(monkeypatch, running_bot(telegram.STARTING, False))
    monkeypatch.setattr(cli, "BOT_START_TIMEOUT", 0)
    cli.main(["telegram-bot", "start", TOKEN, "42"])
    assert capsys.readouterr().out == "Telegram bot: starting; `kanso status` tells how it goes\n"


@pytest.mark.parametrize(
    "reload, error",
    [
        (SyncError("Connection lost"), "The kanso daemon runs an older kanso: restart it"),
        ({"error": "The kanso daemon has no feature 'telegram-bot'"}, "has no feature"),
    ],
)
def test_starting_the_bot_on_a_daemon_without_it_fails(data_dir, monkeypatch, reload, error):
    daemon(monkeypatch, reload=reload)
    with pytest.raises(SystemExit, match=error):
        cli.main(["telegram-bot", "start", TOKEN, "42"])


def test_starting_the_bot_without_a_daemon_fails(data_dir, monkeypatch):
    answers(monkeypatch, start=False)
    with pytest.raises(SystemExit, match="Saved, but the kanso daemon didn't start, so neither did the bot"):
        cli.main(["telegram-bot", "start", TOKEN, "42"])


@pytest.mark.parametrize(
    "argv, error",
    [
        ([TOKEN, "42", "--at", "25:00"], "not a time"),
        ([TOKEN, "42", "--at", "noon"], "not a time"),
        ([TOKEN, "me"], "invalid int value"),
        (["abc", "42"], "not a bot token"),
        (["1:a/b", "42"], "not a bot token"),
        ([TOKEN], "required: CHAT_ID"),
    ],
)
def test_bad_bot_settings_are_refused(data_dir, capsys, argv, error):
    with pytest.raises(SystemExit) as exit:
        cli.main(["telegram-bot", "start", *argv])
    assert exit.value.code == 2 and error in capsys.readouterr().err
    assert not data_dir.exists()


def test_stopping_the_bot_forgets_it_and_tells_the_daemon(data_dir, monkeypatch, capsys):
    asked = daemon(monkeypatch, running_bot())
    cli.main(["telegram-bot", "stop"])
    assert capsys.readouterr().out == "The Telegram bot isn't configured\n" and asked == []
    cli.main(["telegram-bot", "start", TOKEN, "42"])
    asked.clear()
    cli.main(["telegram-bot", "stop"])
    assert capsys.readouterr().out.endswith("Stopped the Telegram bot\n") and bot_config(data_dir) is None
    assert asked == [("reload", {"feature": "telegram-bot"})]


def test_stopping_the_bot_without_a_daemon_just_forgets_it(data_dir, monkeypatch, capsys):
    store = Store()
    telegram.configure(store, TOKEN, 42, "09:00")
    store.close()
    cli.main(["telegram-bot", "stop"])
    assert capsys.readouterr().out == "Stopped the Telegram bot\n" and bot_config(data_dir) is None


def test_stopping_the_bot_on_an_older_daemon_says_so(data_dir, monkeypatch):
    daemon(monkeypatch, reload=SyncError("Connection lost"))
    store = Store()
    telegram.configure(store, TOKEN, 42, "09:00")
    store.close()
    with pytest.raises(SystemExit, match="Removed its config, but: The kanso daemon runs an older kanso"):
        cli.main(["telegram-bot", "stop"])


@pytest.mark.parametrize("bot", [running_bot(), running_bot("can't reach Telegram, retrying", False)])
def test_status_tells_how_the_bot_runs(data_dir, monkeypatch, capsys, bot):
    daemon(monkeypatch, bot)
    cli.main(["status"])
    assert capsys.readouterr().out.splitlines()[-1] == f"Telegram bot: {bot['note']}"


def test_status_tells_a_configured_bot_is_off_without_the_daemon(data_dir, capsys):
    store = Store()
    telegram.configure(store, TOKEN, 42, "09:00")
    store.close()
    cli.main(["status"])
    assert capsys.readouterr().out.splitlines()[-1] == "Telegram bot: configured, but off while the daemon isn't running"


def test_the_daemon_runs_the_bot_feature(data_dir, monkeypatch):
    ran = []

    async def run(self, features):
        ran.append(features)

    monkeypatch.setattr(Node, "run", run)
    cli.main(["start", "--foreground"])
    assert ran == [[control.serve, telegram.serve]]


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


@pytest.mark.parametrize("name", ["start", "stop", "status", "connect", "telegram-bot", "version", "help"])
def test_each_command_explains_itself(data_dir, capsys, name):
    forms = [help_text(capsys, argv) for argv in ([name, "help"], ["help", name], [name, "--help"])]
    assert forms[0] == forms[1] == forms[2]
    assert forms[0].startswith(f"usage: kanso {name}")
    assert not data_dir.exists()


def test_the_bot_command_alone_explains_itself(data_dir, capsys):
    assert help_text(capsys, ["telegram-bot"]) == help_text(capsys, ["telegram-bot", "help"])
    assert not data_dir.exists()


@pytest.mark.parametrize("name", ["start", "stop"])
def test_each_bot_command_explains_itself(data_dir, capsys, name):
    forms = [help_text(capsys, ["telegram-bot", name, *end]) for end in (["help"], ["--help"])]
    assert forms[0] == forms[1] and forms[0].startswith(f"usage: kanso telegram-bot {name}")
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
