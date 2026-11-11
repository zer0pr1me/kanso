"""kanso: a todo app that syncs between your devices.

Its daemon runs in the background, storing your data and syncing it, and runs the features
you start, such as a Telegram bot. With no command, `kanso` opens the terminal UI on it,
starting the daemon if it isn't running."""

import argparse
import asyncio
import re
import sys
from datetime import datetime

from kanso import __version__, control
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync import node as sync_node
from kanso.sync.protocol import CODE_TTL, new_code
from kanso.sync.protocol import version as sync_version
from kanso.sync.transport import SyncError
from kanso.telegram import bot as telegram
from kanso.tui import app as tui

BOT_START_TIMEOUT = 15
"""Seconds `kanso telegram-bot start` waits to hear how the bot started."""


async def open_tui(store: Store, args) -> str | None:
    node = await control.link(store)
    async with node:
        await tui.run(node)


async def start(store: Store, args) -> str | None:
    if args.foreground:
        return await Node(store, sync_node.start).run([control.serve, telegram.serve])
    if await control.running(store):
        print("The kanso daemon already runs")
    elif await control.start(store):
        print("Started the kanso daemon")
    else:
        return f"The kanso daemon didn't start: see {control.log_file(store)}"


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
    if node.sync_note is not None and telegram.config(store):
        print(f"{telegram.TITLE}: configured, but off while the daemon isn't running")


async def reload(store: Store, feature: str):
    """Have the daemon re-read `feature`'s config; raises `SyncError` if it can't."""
    try:
        reply = await control.ask(store, "reload", feature=feature)
    except SyncError:
        raise SyncError("The kanso daemon runs an older kanso: restart it with `kanso stop` and `kanso start`") from None
    if "error" in reply:
        raise SyncError(reply["error"])


async def telegram_bot_start(store: Store, args) -> str | None:
    telegram.configure(store, args.token, args.chat_id, args.at)
    if not await control.start(store):
        return f"Saved, but the kanso daemon didn't start, so neither did the bot: see {control.log_file(store)}"
    try:
        await reload(store, telegram.NAME)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + BOT_START_TIMEOUT
        while True:
            bot = (await control.ask(store, "status")).get("features", {}).get(telegram.NAME)
            if bot and bot["note"] != telegram.STARTING or loop.time() >= deadline:
                break
            await asyncio.sleep(0.2)
    except SyncError as error:
        return str(error)
    if bot is None or bot["note"] == telegram.STARTING:
        print(f"{telegram.TITLE}: starting; `kanso status` tells how it goes")
    elif not bot["running"]:
        return f"{telegram.TITLE}: {bot['note']}"
    else:
        print(f"{telegram.TITLE}: {bot['note']}")


async def telegram_bot_stop(store: Store, args) -> str | None:
    if not telegram.unconfigure(store):
        print(f"The {telegram.TITLE} isn't configured")
        return
    if await control.running(store):
        try:
            await reload(store, telegram.NAME)
        except SyncError as error:
            return f"Removed its config, but: {error}"
    print(f"Stopped the {telegram.TITLE}")


def version_text() -> str:
    """This kanso's version, and the sync version peers compare (`protocol.version`)."""
    protocol, schema = sync_version()
    return f"kanso {__version__} (sync protocol {protocol}, schema {schema})"


def show_version(args):
    print(version_text())


def bot_token(text: str) -> str:
    if not re.fullmatch(r"\d+:[\w-]+", text):
        raise argparse.ArgumentTypeError("not a bot token, which looks like 123456:ABC-DEF…")
    return text


def clock_time(text: str) -> str:
    try:
        return f"{datetime.strptime(text, '%H:%M'):%H:%M}"
    except ValueError:
        raise argparse.ArgumentTypeError("not a time, such as 09:00") from None


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
        "syncs it with your other devices, also while no UI is open; it logs to daemon.log in\n"
        "the data directory.",
    ).add_argument(
        "--foreground", action="store_true", help="run it in the foreground instead, until SIGTERM or ctrl+c, as for systemd"
    )
    command("stop", stop, "stop the daemon", "Stop the daemon. UIs open on it keep working, without sync.")
    command(
        "status",
        status,
        "show whether the daemon runs, and how many devices are online",
        "Show whether the daemon runs, this device's ID, how many of the paired devices it\n"
        "reached in its last round, and when it last synced; if this device or another one\n"
        "runs an older kanso than the rest, which needs an update to sync fully; and, if you\n"
        "started the Telegram bot, how it runs.",
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
    bot = command(
        "telegram-bot",
        None,
        "send the day's todos and yesterday's habits to Telegram, to tick off there",
        "Run a Telegram bot in this device's daemon. Every day it sends you the todos for the day\n"
        "(and older ones still open), and yesterday's habits; tap a todo to tick it off, ✓ or ✗ to\n"
        "mark a habit. /today and /habits send them on demand. It answers only your chat.\n\n"
        "  kanso telegram-bot start TOKEN CHAT_ID [--at HH:MM]    start it, or change its settings\n"
        "  kanso telegram-bot stop                               stop it, and forget its settings\n\n"
        "Make a bot with @BotFather on Telegram, which gives its token, and send it /start.\n"
        "Your chat ID is your numeric Telegram user ID (@userinfobot tells it); the bot also tells\n"
        "anyone sending it /start their chat's ID. Start it on one device only: Telegram lets one\n"
        "program at a time use a bot. `kanso status` shows how it runs.",
    )
    bot.set_defaults(helps={None: bot}, topic=None)
    actions = bot.add_subparsers(title="commands", metavar="<command>", help="what to do with the bot")
    start_bot = actions.add_parser(
        "start",
        help="start the bot, or change its settings",
        description="Start the Telegram bot in the daemon (starting the daemon if needed), or restart it with\n"
        "new settings, and tell how it started.",
        **formatting,
    )
    start_bot.set_defaults(command=telegram_bot_start)
    start_bot.add_argument("token", type=bot_token, metavar="TOKEN", help="the bot's token, from @BotFather")
    start_bot.add_argument("chat_id", type=int, metavar="CHAT_ID", help="the ID of your chat with the bot: the only one it answers")
    start_bot.add_argument("--at", type=clock_time, metavar="HH:MM", default="09:00", help="when to send the day's message, local time (default: 09:00)")
    actions.add_parser(
        "stop", help="stop the bot, and forget its settings", description="Stop the Telegram bot, and forget its token and chat."
    ).set_defaults(command=telegram_bot_stop)
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
