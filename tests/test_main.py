import os
import signal
import threading
import time

import pytest

from conftest import sync_over
from kanso import __main__ as cli
from kanso import control
from kanso.core.store import Store
from kanso.node import Node
from kanso.sync import node as sync_node
from kanso.sync.node import owner_lock


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KANSO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sync_node, "start", sync_over({}))
    return tmp_path / "data"


def answers(monkeypatch, **results):
    """Make `control`'s async functions named in `results` return them."""
    for name, result in results.items():

        async def answer(store, result=result):
            return result

        monkeypatch.setattr(control, name, answer)


def test_kanso_opens_the_tui_on_the_linked_node(data_dir, monkeypatch):
    ran = []

    async def link(store):
        return Node(store)

    async def tui(node):
        ran.append(node.day is not None and node.store.lists() == [])

    monkeypatch.setattr(control, "link", link)
    monkeypatch.setattr(cli.tui, "run", tui)
    cli.main([])
    assert ran == [True]


@pytest.mark.parametrize(
    "running, started, output",
    [(True, None, "The kanso daemon already runs\n"), (False, True, "Started the kanso daemon\n")],
)
def test_start(data_dir, monkeypatch, capsys, running, started, output):
    answers(monkeypatch, running=running, start=started)
    cli.main(["start"])
    assert capsys.readouterr().out == output


def test_start_reports_a_daemon_that_didnt_start(data_dir, monkeypatch):
    answers(monkeypatch, running=False, start=False)
    with pytest.raises(SystemExit, match=f"didn't start: see {data_dir / 'daemon.log'}"):
        cli.main(["start"])


@pytest.mark.parametrize("ran, output", [(True, "Stopped the kanso daemon\n"), (False, "The kanso daemon isn't running\n")])
def test_stop(data_dir, monkeypatch, capsys, ran, output):
    answers(monkeypatch, stop=ran)
    cli.main(["stop"])
    assert capsys.readouterr().out == output


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_the_foreground_daemon_runs_until_sigterm(data_dir, capsys):
    def stop_once_up():
        deadline = time.monotonic() + 5
        while not (data_dir / "daemon.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=stop_once_up).start()
    cli.main(["start", "--foreground"])
    assert capsys.readouterr().out == "kanso daemon syncing as key-app\n"
    assert not (data_dir / "daemon.json").exists()


def test_the_foreground_daemon_refuses_when_another_runs(data_dir):
    store = Store()
    lock = owner_lock(store)  # held by "another daemon"; outlives its store
    store.close()
    with pytest.raises(SystemExit, match="Another kanso daemon syncs this data directory"):
        cli.main(["start", "--foreground"])
    lock.close()
