import os
import shlex
import sys

from kanso import control, web


def test_each_tab_runs_this_very_kanso(monkeypatch):
    served = []

    class Server:
        def __init__(self, command, port):
            served.append((command, port, os.environ["PYTHONPATH"]))

        def serve(self):
            pass

    monkeypatch.setattr(web, "Server", Server)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    web.main()
    kanso, env = control.command()
    assert served == [(shlex.join([sys.executable, "-m", "kanso"]), 8080, env["PYTHONPATH"])]
