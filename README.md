# kanso

Personal, local-first todo app with a terminal UI, also served in the browser.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Run

```sh
uv sync
uv run kanso          # terminal UI
uv run kanso-web      # same UI in the browser on http://localhost:8080
```

Data is stored in a local SQLite database in the user data directory
(`~/.local/share/kanso/kanso.db` on Linux).

## Develop

```sh
uv run pytest
scripts/install-hooks.sh   # once per clone: run the tests before every commit
```
