# Architecture

Personal, single-user, local-first todo app, rewritten from scratch in `src/kanso/` (`v1/` is history only, not a reference). No server, no auth: each instance owns its SQLite database. Syncing between instances (possibly P2P, e.g. libp2p) comes later.

- `core/`: no UI dependencies; the part future frontends (web, mobile) build on.
  - `store.py`: `Store`, the database and every operation on it. UIs call only this.
  - `models.py`: SQLModel tables, kept sync-ready: UUID ids, tombstone deletes (`deleted`), `modified_at` hybrid-logical-clock stamps (`clock.py`) for last-writer-wins, and fractional-index string positions (`position.py`). Preserve these properties in every change.
- `tui/`: Textual app, the only UI.
- `web.py`: serves the same Textual app in the browser via textual-serve. No separate web frontend.

How to run the app is documented in `README.md`. Keep it up to date whenever commands, ports, requirements, or setup steps change.

# Code style

- Concise and minimal: no boilerplate, no speculative abstractions or unrequested features.
- Reusable: reuse existing helpers before writing new ones; extract repeated logic into shared helpers.

# Testing

- Test code thoroughly: every behavior, including error paths and validation.
- Tests live in `tests/`; run `uv run pytest` and keep it green (warnings are errors).
