"""kanso: a todo app that syncs between your devices.

Its daemon runs in the background, storing your data and syncing it, and runs the features
you start. With no command, `kanso` opens the terminal UI on it,
starting the daemon if it isn't running."""

import argparse
import asyncio
import logging
import sys

from kanso import __version__, control, log
from kanso.core.store import Store, default_path
from kanso.node import Node, which_kanso
from kanso.sync import node as sync_node
from kanso.sync.protocol import CODE_TTL, new_code
from kanso.sync.protocol import version as sync_version
from kanso.sync.transport import SyncError
from kanso.tui import app as tui

logger = logging.getLogger("kanso.cli")


async def open_tui(store: Store, args) -> str | None:
    node = await control.link(store)
    async with node:
        await tui.run(node)


async def start(store: Store, args) -> str | None:
    daemon = await control.daemon_status(store)
    if args.foreground:
        if daemon:
            if not control.replaceable(daemon, take_over=True):
                print(f"The kanso daemon runs {which_kanso(control.daemon_of(daemon))}, of a newer sync version: left it running")
                return None
            logger.info("Taking over from the running daemon (kanso %s, build %s)", daemon.get("version"), daemon.get("build"))
            await control.replace(store)
        return await Node(store, sync_node.start).run([control.serve])
    if daemon and not control.replaceable(daemon):
        print("The kanso daemon already runs")
    elif not await control.start(store):
        return f"The kanso daemon didn't start: see {control.log_file(store)}"
    elif daemon:
        print(f"Restarted the kanso daemon with this kanso: it ran {which_kanso(control.daemon_of(daemon))}")
    else:
        print("Started the kanso daemon")


async def stop(store: Store, args) -> str | None:
    print("Stopped the kanso daemon" if await control.stop(store) else "The kanso daemon isn't running")


async def connect(store: Store, args) -> str | None:
    if (args.id is None) != (args.code is None):
        return "Give both the other device's ID and its code, or neither"
    if not await control.start(store):
        return f"The kanso daemon didn't start, so it can't connect: see {control.log_file(store)}"
    try:
        key = (await control.ask(store, "status"))["key"]
        if args.id is None:
            code = new_code(store)
            print(f"This device: {key}\nCode: {code}, valid {CODE_TTL // 60} minutes, once")
            print(f"On the other device, run: kanso connect {key} {code}")
        elif args.id == key:
            return "That's this device's own ID"
        else:
            print(f"Connected to {await control.NodeClient(store).pair(args.id, args.code)}")
    except SyncError as error:
        return str(error)


async def status(store: Store, args) -> str | None:
    node = control.NodeClient(store)
    try:
        node.update(await control.ask(store, "status"))
    except SyncError:
        node.stopped()
    paired = sum(device.key != node.key for device in store.devices())
    print(f"Daemon: {'running, syncing' if node.sync_note is None else 'not running, so sync is off'}")
    print(f"This device: {node.key}")
    print(f"Devices online: {node.online} of {paired} paired")
    print(f"Last synced: {f'{node.synced_at:%Y-%m-%d %H:%M}' if node.synced_at else 'not yet'}")
    for note in node.update_notes():
        print(note)
    for feature in node.feature_status.values():
        print(f"{feature['title']}: {feature['note']}")
    print(f"Log: {control.log_file(store)}")


def version_text() -> str:
    """This kanso's version, and the sync version peers compare (`protocol.version`)."""
    protocol, schema = sync_version()
    return f"kanso {__version__} (sync protocol {protocol}, schema {schema})"


def show_version(args):
    print(version_text())


def parser() -> argparse.ArgumentParser:
    """The CLI: every command and flag with its help, shown by `kanso help [command]`,
    `kanso <command> help` and `--help`."""
    formatting = {"formatter_class": argparse.RawDescriptionHelpFormatter}
    root = argparse.ArgumentParser(
        prog="kanso",
        usage="kanso [<command>] [help]",
        description=__doc__,
        epilog="Run `kanso <command> help` (or `kanso help <command>`) for a command's own help.",
        **formatting,
    )
    root.set_defaults(command=open_tui)
    root.add_argument("--version", action="version", version=version_text(), help="show kanso's version, as `kanso version` does")
    commands = root.add_subparsers(title="commands", metavar="<command>")

    def command(name: str, run, help: str, description: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help, description=description, **formatting)
        sub.set_defaults(command=run)
        return sub

    command(
        "start",
        start,
        "start the daemon in the background",
        "Start the daemon in the background, if it isn't running yet. It stores your data and\n"
        "syncs it with your other devices, also while no UI is open; it logs to kanso.log in\n"
        "the data directory, as every kanso process does (KANSO_LOG=debug tells more).\n\n"
        "A daemon keeps running the kanso it started with, so this, and opening kanso, restarts\n"
        "one running another kanso (newer, older or a dev build) with this one, unless it speaks\n"
        "a newer sync version, which this kanso can't follow.",
    ).add_argument(
        "--foreground",
        action="store_true",
        help="run it in the foreground instead, until SIGTERM or ctrl+c, as for systemd; replaces a running daemon",
    )
    command("stop", stop, "stop the daemon", "Stop the daemon. UIs open on it keep working, without sync.")
    command(
        "status",
        status,
        "show whether the daemon runs, and how many devices are online",
        "Show whether the daemon runs, this device's ID, how many of the paired devices it\n"
        "reached in its last round, and when it last synced; if this device or another one\n"
        "runs an older kanso than the rest, which needs an update to sync fully; how each\n"
        "feature you started in the daemon runs; and where the log is.",
    )
    pair = command(
        "connect",
        connect,
        "pair with another device: show this one's ID and code, or connect to another's",
        "Pair this device with another, joining their networks: from then on they sync with\n"
        "each other and every device either one already syncs with. Starts the daemon if it\n"
        "isn't running.\n\n"
        "  kanso connect            show this device's ID and a fresh pairing code\n"
        "  kanso connect ID CODE    connect to the device with that ID, using its code",
    )
    pair.add_argument("id", nargs="?", help="the other device's ID, as its `kanso connect` shows it")
    pair.add_argument("code", nargs="?", help="the other device's pairing code")
    command(
        "version",
        show_version,
        "show kanso's version",
        "Show this kanso's version, and the sync protocol and data schema it speaks: devices\n"
        "sync fully when these match, and the older one is told to update otherwise.",
    )
    explain = command("help", None, "show this help, or a command's", "Show kanso's help, or that of a command.")
    explain.add_argument("topic", nargs="?", choices=list(commands.choices), metavar="command", help="the command to explain")
    explain.set_defaults(helps={None: root, **commands.choices})
    return root


def main(argv: list[str] | None = None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[-1:] == ["help"] and argv[:1] != ["help"]:  # kanso <command> help
        argv = [*argv[:-1], "--help"]
    args = parser().parse_args(argv)
    if args.command is None:  # kanso help [command]
        args.helps[args.topic].print_help()
        return
    if args.command is show_version:  # needs no store
        show_version(args)
        return
    daemon = args.command is start and args.foreground
    role = "daemon" if daemon else "tui" if args.command is open_tui else "cli"
    log.setup(default_path().parent, role, rotate=daemon, stderr=daemon)
    store = Store()
    try:
        error = asyncio.run(args.command(store, args))
    except KeyboardInterrupt:
        error = None
    finally:
        store.close()
    if error:
        sys.exit(error)


if __name__ == "__main__":
    main()
