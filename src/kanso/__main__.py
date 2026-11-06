"""`kanso`: runs this device's node with the interfaces chosen by flags (the TUI by default)."""

import argparse
import asyncio
import os
import signal
import sys

from kanso.core.store import Store
from kanso.node import Interface, Node
from kanso.sync import node as sync_node
from kanso.tui import app as tui


async def headless(node: Node) -> str | None:
    """Sync until stopped (ctrl+c or SIGTERM); returns why not if another process owns the data dir."""
    if not node.sync:
        return "Another kanso process already syncs this data directory"
    stop = asyncio.Event()
    if os.name != "nt":  # there, ctrl+c raises KeyboardInterrupt instead
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    print(f"kanso is syncing as {node.key}; ctrl+c stops", flush=True)
    await stop.wait()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="kanso", description=__doc__)
    parser.add_argument("--no-tui", action="store_true", help="run without the terminal UI: just store and sync")
    args = parser.parse_args(argv)
    interfaces: list[Interface] = [headless] if args.no_tui else [tui.run]
    store = Store()
    try:
        error = asyncio.run(Node(store, sync_node.start).run(interfaces))
    except KeyboardInterrupt:
        error = None
    finally:
        store.close()
    if error:
        sys.exit(error)


if __name__ == "__main__":
    main()
