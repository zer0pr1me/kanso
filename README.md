# kanso

Multi-user todo app with a terminal UI, also served in the browser.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Run

```sh
uv sync
uv run kanso-server   # API server on http://localhost:8000
uv run kanso          # terminal UI
uv run kanso-web      # same UI in the browser on http://localhost:8080
```

Start the server before running either UI.

## Develop

```sh
uv run pytest
```
