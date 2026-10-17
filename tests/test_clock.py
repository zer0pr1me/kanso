from kanso.core import clock
from kanso.core.clock import Clock


def freeze(monkeypatch, millis: int):
    monkeypatch.setattr(clock.time, "time_ns", lambda: millis * 1_000_000)


def test_stamps_follow_wall_time(monkeypatch):
    c = Clock("n1")
    freeze(monkeypatch, 1000)
    first = c.now()
    freeze(monkeypatch, 2000)
    assert first == "000000000001000-000000-n1"
    assert c.now() == "000000000002000-000000-n1"


def test_same_millisecond_increments_counter(monkeypatch):
    c = Clock("n1")
    freeze(monkeypatch, 1000)
    assert c.now() < c.now() < c.now()


def test_never_goes_backwards_when_wall_clock_does(monkeypatch):
    c = Clock("n1")
    freeze(monkeypatch, 2000)
    first = c.now()
    freeze(monkeypatch, 1000)
    assert c.now() == "000000000002000-000001-n1" > first


def test_resumes_after_last_stamp(monkeypatch):
    freeze(monkeypatch, 1000)
    assert Clock("n1", "000000000005000-000003-n1").now() == "000000000005000-000004-n1"


def test_node_breaks_ties(monkeypatch):
    freeze(monkeypatch, 1000)
    assert Clock("a").now() < Clock("b").now()
