import asyncio

import pytest

from kanso.core.store import Store


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
    return [t.text for t in store.todos(list_id)]


def run(app, check):
    """Run `app` headless and await `check(app, pilot)` once it has settled."""

    async def main():
        async with app.run_test() as pilot:
            await pilot.pause()
            await check(app, pilot)

    asyncio.run(main())


def notifications(app) -> list[str]:
    return [n.message for n in app._notifications]
