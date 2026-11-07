# Architecture

Personal, single-user, local-first todo app, rewritten from scratch in `src/kanso/` (`v1/` is history only, not a reference). No server, no accounts: each instance owns its SQLite database, and paired devices sync peer to peer over iroh.

Kanso is a P2P node at its base, in one executable, `kanso`. The node runs as a daemon in the background, one per data dir, storing the data and syncing it. Features run inside the daemon and are switched on or off there: the Telegram bot (`kanso telegram-bot start|stop`); planned: `kanso web-app start|stop`, an API. Only the TUI runs in the foreground: it is plain `kanso`, which starts the daemon if needed and attaches to it. Interfaces use only a `NodeView` and its `Store`, never the sync internals, so they stay independent of each other and of networking.

- `node.py`: `NodeView` is what interfaces see: store, device key, sync status, `subscribe` for changes from elsewhere, and `pair`. `Node` is the node itself. It owns the store, sync (when its process owns the data dir, via `owner_lock`) and the day rollover (`check_day` spawns each new day's instances). `Node.run` runs the daemon's features, each an `async (node) -> result`, until the first one returns. A feature with settings registers a `Feature` in `Node.features` while it runs: its `status()` (`None` while not configured, so interfaces say nothing of it) reaches interfaces as `NodeView.feature_status`, and `reload()` re-reads its settings after the CLI changed them.
- `control.py`: the daemon (`serve`, its control feature) and how other processes reach it. It listens on localhost; `daemon.json` holds the port and the token. One JSON request per connection: `watch`, `status`, `pair`, `reload` (a feature) or `stop`. `NodeClient` is the `NodeView` for other processes: the data goes through their own `Store` on the shared database, and status and pairing go through the daemon. `link` starts the daemon if none runs, or falls back to a local `Node` without sync.
- `__main__.py`: the CLI: `kanso` (the TUI), `kanso start [--foreground]`, `kanso stop`, `kanso status`, `kanso connect [ID CODE]` (pairing without the TUI), `kanso telegram-bot start TOKEN CHAT_ID [--at HH:MM]|stop`, `kanso help [command]` (also `kanso <command> help`). `parser()` builds it. A feature's commands save its settings in the store and `reload` it in the daemon.
- `core/`: no UI dependencies; the part every interface (TUI, Telegram bot; future web, mobile) builds on. Shared presentation helpers that need no UI library, such as `order.human_date`, live here too.
  - `store.py`: `Store`, the database and every operation on it. UIs call only this.
    Several processes (the daemon, TUIs) share the database, each with its own `Store`. Every public method is one `operation`: a write-locked transaction that first drops the cached records if another process changed the database since. Keep any new public method that way, and keep the session from holding transactions between operations.
    Records in a deleted or missing list count as deleted, so rows from replicas never break reads.
  - `order.py`: how todos are grouped by date and autosorted; add autosort rules to `autosort_key`.
  - Templates (`models.Scheduled`: repeats and habits) spawn instances (todos and habit days) in `Store.spawn`, with ids derived from template and day so replicas agree.
  - `models.py`: SQLModel tables, kept sync-ready: UUID ids, tombstone deletes (`deleted`), per-field hybrid-logical-clock stamps (`stamps`, set by `Store._save`; `clock.py`) merged field by field, last writer wins (`merge.py`), and fractional-index string positions (`position.py`). Preserve these properties in every change.
  - `migrations.py`: every change to the table models needs a migration appended to `MIGRATIONS`, written as a per-row data transform (it also upgrades rows synced from older replicas). Never edit a released migration.
  - `sync.py`: raw rows and digests as replicas exchange them; `Store.digest`/`export`/`merge_rows` use it. New synced tables go in `SYNCED_MODELS`.
- `sync/`: networking, no UI dependencies.
  - `transport.py`: `Transport` dials and accepts peers by public key; `IrohTransport` for real, `MemoryTransport` for tests. `Channel` carries length-prefixed JSON.
  - `protocol.py`: one session (hello, digests, rows) and pairing codes. Synced `Device` rows are the trust list.
    Versions: hello and welcome carry each peer's `version()` (`PROTOCOL`, schema) and never change shape; the ALPN never changes either. Peers of the same version always sync fully; otherwise the older one is told to update (`SyncNode.outdated`/`behind`, shown by every interface via `NodeView.update_notes`). So:
    - A change to the session after welcome that the current version can't follow bumps `PROTOCOL`; peers of different protocols stop after welcome. A new optional field doesn't.
    - A change to the synced data, including only to what a field means, how instance ids derive, or how stamps compare and merge, needs a migration (even one leaving rows as they are), so the schema version moves.
    - Anything else (UI, CLI, daemon, features) is free: it doesn't affect sync.
  - `node.py`: `SyncNode` serves peers and syncs with every device on start, after local changes and periodically; `owner_lock` makes one process per data dir the one that syncs.
- `telegram/`: the Telegram bot, a daemon feature (`bot.serve`) using only the `NodeView` and its `Store`. Its settings are per replica in `Store.meta`, never synced. `api.py` is a small Bot API client on aiohttp; tests use a fake one, never Telegram.
- `tui/`: Textual app, the only interface so far; `tui.app.run` is its entry on a `NodeView`.
- `web.py`: `kanso-web`, which serves the TUI in the browser via textual-serve, with one `kanso` TUI process per tab attached to the daemon. It is still separate for now: the real web interface should become a daemon feature (`kanso web-app start`).

How to run the app is documented in `README.md`. Keep it up to date whenever commands, ports, requirements, or setup steps change.

The CLI's help is part of every feature. Each command (added with `command` in `parser()`) gets a one-line help for the command list and a description for its own help, and each flag or argument gets a help. Change them whenever behavior changes, and keep the module docstring (the top-level help) true. `test_every_command_and_flag_has_help` fails on a missing help.

# Code style

- Concise and minimal: no boilerplate, no speculative abstractions or unrequested features.
- Reusable: reuse existing helpers before writing new ones; extract repeated logic into shared helpers.

# Testing

- Test code thoroughly: every behavior, including error paths and validation.
- Tests live in `tests/`; run `uv run pytest` and keep it green (warnings are errors).
- Network tests (real iroh) run only with `KANSO_NETWORK_TESTS=1`; everything else uses `MemoryTransport` and must stay offline.
