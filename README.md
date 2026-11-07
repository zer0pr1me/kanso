# kanso

Personal, local-first todo app with a terminal UI, also served in the browser, and a
Telegram bot.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Linux (x86_64 or aarch64), macOS (Apple silicon) or Windows (x86_64), the platforms
  that [iroh](https://iroh.computer), used for peer-to-peer sync, ships Python wheels for

## Run

```sh
uv sync
uv run kanso          # terminal UI, starting the daemon if it isn't running
uv run kanso start    # just start the daemon, in the background
uv run kanso stop     # stop it
uv run kanso status   # whether it runs, and how many devices are online
uv run kanso telegram-bot start TOKEN CHAT_ID   # see "Telegram bot" below
uv run kanso help     # every command, briefly; `kanso <command> help` for one
uv run kanso-web      # same UI in the browser on http://localhost:8080
```

kanso's daemon runs in the background: it stores your data and syncs it with
your other devices, also while no UI is open. `kanso` opens the terminal UI on
it; quitting the UI leaves the daemon running. Each data directory has one
daemon, however many UIs are open on it. On an always-online device, such as a
home server, the others can sync through it. To run the daemon from a service
manager such as systemd, use `kanso start --foreground`, which stops on SIGTERM.
It logs to `daemon.log` in the data directory.

Data is stored in a local SQLite database in the user data directory
(`~/.local/share/kanso/kanso.db` on Linux). Set `KANSO_DATA_DIR` to keep it
elsewhere: `KANSO_DATA_DIR=~/kanso uv run kanso`.

## Sync between devices

Devices sync peer to peer while their kanso daemons run. To join two devices, and
with them every device each one already syncs with:

1. On one device, press `c`. It shows this device's ID and a pairing code,
   valid for 10 minutes and usable once (`ctrl+y` copies both).
2. On the other device, press `c` and enter that ID and code.

Without the UI, e.g. on a home server over ssh, `kanso connect` shows the ID and
a code, and `kanso connect <ID> <code>` connects to another device; both start
the daemon if it isn't running.

From then on, changes reach every device within seconds when they're online,
and as soon as they come back otherwise. When the same field of a todo was
edited on two devices meanwhile, the later edit wins; edits to different fields
both survive. The header, and `kanso status`, show how many devices are online.

- Connections use [iroh](https://iroh.computer): devices find each other by ID,
  through NATs, with help from n0's public relays and DNS. Traffic is encrypted
  end to end; the relays see only device IDs and IP addresses.
- Paired devices are fully trusted: any of them can change or delete anything.
- The daemon is the only kanso process of its data directory that syncs (a
  `sync.lock` there says which); UIs, such as browser tabs of `kanso-web`, share
  its data. UIs reach it on localhost, with a token from `daemon.json` in the
  data directory, readable only by you. If the daemon can't start, the UI still
  works, without sync.
- Don't copy the data directory to another machine, or keep it in a synced
  folder: the copy would be the same device as far as sync is concerned. Pair
  the machines instead.
- Devices running different versions of kanso still sync as far as they can: a
  newer one takes the changes of an older one, but not the other way round. The
  older one asks you to update kanso, and the newer one names the devices that
  need it, in the header and in `kanso status`.

## Telegram bot

The daemon can run a Telegram bot that brings your day to Telegram. Every day at
09:00 (local time) it sends the todos for the day, with older ones still open,
from every list; tap one to tick it off, or back. It also sends yesterday's
habits, if some aren't marked yet: tap ✓ or ✗ to mark one, again to unmark it.
`/today` and `/habits` send them whenever you ask.

1. In Telegram, make a bot with [@BotFather](https://t.me/BotFather): it gives
   you the bot's token. Send your new bot `/start`, so it may message you.
2. Find your chat ID, your numeric Telegram user ID: ask
   [@userinfobot](https://t.me/userinfobot), or start the bot with any ID and
   send it `/start`, as it tells strangers their chat's ID.
3. Run `kanso telegram-bot start TOKEN CHAT_ID`, adding `--at 07:30` for
   another time. It starts the daemon if needed, and tells whether the bot runs.

The bot answers only that chat. Its settings stay on this device, never synced,
so start it on one device only, ideally one that's always on: Telegram lets one
program at a time use a bot. It runs whenever the daemon does; `kanso status`
shows how it runs, or what's wrong. Run `kanso telegram-bot start` again to
change its settings, and `kanso telegram-bot stop` to stop it and forget them.

## Install on NixOS

Try it without installing:

```sh
nix run github:zer0pr1me/kanso
```

To install it, add the flake as an input of your system flake and pass it to
home-manager (for example via `home-manager.extraSpecialArgs`):

```nix
inputs.kanso = {
  url = "github:zer0pr1me/kanso";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

Then, in a home-manager user configuration:

```nix
{ kanso, config, ... }:
{
  imports = [ kanso.homeManagerModules.default ];

  programs.kanso = {
    enable = true;
    # Optional; defaults to ~/.local/share/kanso.
    dataDir = "${config.home.homeDirectory}/kanso";
  };
}
```

This installs both `kanso` and `kanso-web`. `dataDir` is baked into them as the
default `KANSO_DATA_DIR`; setting the variable yourself still takes precedence.
The package is built by `nix build` (for `x86_64-linux` and `aarch64-linux`),
which also runs the test suite.

## Develop

```sh
uv run pytest         # in parallel; add -n0 to run serially, e.g. to use a debugger
KANSO_NETWORK_TESTS=1 uv run pytest   # also connect real iroh nodes (needs internet)
scripts/install-hooks.sh   # once per clone: run the tests before every commit
```
