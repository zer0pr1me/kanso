# Changelog

Versions follow [semantic versioning](https://semver.org), as kept in `CLAUDE.md`: a new
data schema or sync protocol is a minor version while kanso is 0.x.

## 0.2.3

- Opening kanso, or `kanso start`, restarts a daemon running another kanso (an older one
  that kept running after an update, a newer one, or a dev build) with this one, unless it
  speaks a newer sync version. `kanso start --foreground` takes over a running daemon too.
  UIs left open attach to the new daemon, and tell when it runs another kanso.
- On Nix, kanso can start its daemon, and `kanso-web` its tabs, at all: they ran a bare
  `python -m kanso`, which can't find kanso there.
- kanso logs: every process appends to `kanso.log` in the data directory (it replaces
  `daemon.log`), telling when the daemon starts and stops and why, which kanso takes it
  over, devices coming online and failing to sync, pairing, upgrades and crashes, but
  never your todos or secrets. `KANSO_LOG=debug` tells more; `kanso status` shows where.
- A device that can't be reached no longer holds up syncing with the others: each device's
  session runs on its own, where a round used to wait for the slowest (up to 30 s and more).
- Escape quits the TUI from the lists; on the right, it goes back to the lists first.
- The home-manager module can run the daemon as a systemd user service
  (`programs.kanso.daemon.enable`), restarted when a switch changes kanso.

## 0.2.2

- Moving an item with `J`/`K` (or after a sync) no longer scrolls the TUI's panes, unless
  the item would go out of view.

## 0.2.1

- The Telegram bot is gone, with `kanso telegram-bot`.

## 0.2.0

- Devices paired through one that is offline now sync with each other: devices vouch for
  each other with signed introductions, synced along with the data (schema 6).
- `kanso version` (and `kanso --version`) shows kanso's version, and the sync protocol
  and schema it speaks.
- The TUI's header shows kanso's version.

## 0.1.0

- First version: the terminal UI (also in the browser with `kanso-web`), the daemon,
  peer-to-peer sync between paired devices, and the Telegram bot.
