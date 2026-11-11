import logging
import re
import sys

import pytest

from kanso import log


def lines(data_dir) -> list[str]:
    return log.path(data_dir).read_text().splitlines()


def test_lines_tell_when_how_bad_which_process_and_module(tmp_path):
    log.setup(tmp_path, "tui")
    logging.getLogger("kanso.sync.node").info("Synced with %s", "zerocore")
    logging.getLogger("kanso.sync.node").debug("not by default")
    [line] = lines(tmp_path)
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO tui\[\d+\] kanso.sync.node: Synced with zerocore", line)


def test_others_warnings_are_logged_but_not_their_chatter(tmp_path):
    log.setup(tmp_path, "cli")
    logging.getLogger("asyncio").info("chatter")
    logging.getLogger("asyncio").warning("Task exception was never retrieved")
    assert [line.split(": ", 1)[1] for line in lines(tmp_path)] == ["Task exception was never retrieved"]


def test_debug_on_demand(tmp_path, monkeypatch):
    monkeypatch.setenv("KANSO_LOG", "DEBUG")
    log.setup(tmp_path, "cli")
    logging.getLogger("kanso.control").debug("more")
    assert re.search(r" DEBUG cli\[\d+\] kanso.control: more$", lines(tmp_path)[0])


def test_a_second_setup_replaces_the_first(tmp_path):
    (tmp_path / "first").mkdir()
    log.setup(tmp_path / "first", "cli")
    log.setup(tmp_path, "daemon")
    logging.getLogger("kanso").info("once")
    assert lines(tmp_path / "first") == [] and " daemon[" in lines(tmp_path)[0]


@pytest.mark.parametrize("rotate", [True, False])
def test_only_the_daemon_rotates(tmp_path, monkeypatch, rotate):
    monkeypatch.setattr(log, "MAX_BYTES", 200)
    log.setup(tmp_path, "daemon" if rotate else "tui", rotate=rotate)
    for n in range(20):
        logging.getLogger("kanso").info("line %d", n)
    assert (tmp_path / "kanso.log.1").exists() == rotate
    assert len(list(tmp_path.glob("kanso.log*"))) <= 1 + log.BACKUPS


def test_stderr_too_if_asked(tmp_path, capsys):
    log.setup(tmp_path, "daemon", stderr=True)
    logging.getLogger("kanso").info("started")
    assert capsys.readouterr().err.endswith("kanso: started\n") and lines(tmp_path)[0].endswith("kanso: started")


def test_not_to_stderr_that_is_the_log(tmp_path, monkeypatch):
    """A spawned daemon's stderr is the log: lines would show twice."""
    log.path(tmp_path).touch()
    with open(log.path(tmp_path), "a") as stderr:
        monkeypatch.setattr(sys, "stderr", stderr)
        log.setup(tmp_path, "daemon", stderr=True)
        logging.getLogger("kanso").info("once")
    assert len(lines(tmp_path)) == 1


def test_crashes_are_logged_and_shown(tmp_path, capsys):
    log.setup(tmp_path, "cli")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    text = log.path(tmp_path).read_text()
    assert "CRITICAL cli[" in text and "RuntimeError: boom" in text and "Traceback" in text
    assert "RuntimeError: boom" in capsys.readouterr().err


def test_keys_are_told_short():
    assert log.short("f377e1c25c9bb30c27989f2459c8d8d3") == "f377e1c25c"
