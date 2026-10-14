# Architecture

Multi-user todo app, rewritten from scratch in `src/kanso/` (`v1/` is history only, not a reference).

- `server/`: FastAPI + SQLModel (SQLite), thin CRUD API, opaque session tokens. Single source of truth.
- `client/`: httpx `Api` wrapper; clients are online-only.
- `tui/`: Textual app, the only UI.
- `web.py`: serves the same Textual app in the browser via textual-serve. No separate web frontend.
- `schemas.py`: Pydantic models shared by server and client.

How to run the app is documented in `README.md`. Keep it up to date whenever commands, ports, requirements, or setup steps change.

# Code style

- Concise and minimal: no boilerplate, no speculative abstractions or unrequested features.
- Reusable: reuse existing helpers before writing new ones; extract repeated logic into shared helpers.
