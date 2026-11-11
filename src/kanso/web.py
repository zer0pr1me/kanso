import os
import shlex

from textual_serve.server import Server

from kanso import control, log
from kanso.core.store import default_path


def main():
    """Serve the TUI on http://localhost:8080: each tab runs this very kanso's TUI."""
    log.setup(default_path().parent, "web")
    kanso, env = control.command()
    os.environ.update(env)  # the tabs' processes inherit it
    Server(shlex.join(kanso), port=8080).serve()
