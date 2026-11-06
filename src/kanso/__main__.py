"""kanso: a todo app that syncs between your devices.

Its node runs as a daemon in the background, storing your data and syncing it, with the
features enabled there; `kanso` opens the terminal UI on it, starting it if needed."""

import argparse
import asyncio
import sys

from kanso import control
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync import node as sync_node
from kanso.tui import app as tui


async def open_tui(store: Store, args) -> str | None:
    node = await control.link(store)
    async with node:
        await tui.run(node)


async def start(store: Store, args) -> str | None:
    if args.foreground:
        return await Node(store, sync_node.start).run([control.serve])
    if await control.running(store):
        print("The kanso daemon already runs")
    elif await control.start(store):
        print("Started the kanso daemon")
    else:
        return f"The kanso daemon didn't start: see {control.log_file(store)}"


async def stop(store: Store, args) -> str | None:
    print("Stopped the kanso daemon" if await control.stop(store) else "The kanso daemon isn't running")


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="kanso", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.set_defaults(command=open_tui)
    commands = parser.add_subparsers(title="commands")
    start_parser = commands.add_parser("start", help="start the daemon in the background")
    start_parser.add_argument("--foreground", action="store_true", help="run it in the foreground instead, as a service manager does")
    start_parser.set_defaults(command=start)
    commands.add_parser("stop", help="stop the daemon").set_defaults(command=stop)
    args = parser.parse_args(argv)
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
