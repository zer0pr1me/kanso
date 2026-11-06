import asyncio
from contextlib import nullcontext

import pytest
import textual._wait

from kanso.core.store import Store


@pytest.fixture(autouse=True)
def quick_idle_checks(monkeypatch):
    """Pilot waits for the app to idle after every key, sleeping 20ms per check; 2ms checks
    find it idle just as well and make the TUI tests several times faster."""
    monkeypatch.setattr(textual._wait, "SLEEP_GRANULARITY", 1 / 500)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "kanso.db"


@pytest.fixture
def store(path):
    s = Store(path)
    yield s
    s.close()


@pytest.fixture
def todo_list(store):
    return store.add_list("Inbox")


def texts(store, list_id) -> list[str]:
    return [t.title for t in store.todos(list_id)]


def run(app, check):
    """Run `app` headless, on its running node if it has one, and await `check(app, pilot)` once it has settled."""

    async def main():
        async with getattr(app, "node", nullcontext()), app.run_test() as pilot:
            await pilot.pause()
            await check(app, pilot)

    asyncio.run(main())


def notifications(app) -> list[str]:
    return [n.message for n in app._notifications]
