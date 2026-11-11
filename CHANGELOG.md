# Changelog

Versions follow [semantic versioning](https://semver.org), as kept in `CLAUDE.md`: a new
data schema or sync protocol is a minor version while kanso is 0.x.

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
