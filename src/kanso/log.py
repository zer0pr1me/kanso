"""kanso's log: `kanso.log` in the data dir, which every kanso process of it appends to.

Each line tells when, how bad, which process (its role and pid) and which module:
`2026-11-11 17:13:12 INFO daemon[683000] kanso.sync.node: Synced with zerocore: 3 rows in`.
INFO tells what changed, such as a daemon starting or a device going offline; `KANSO_LOG=debug`
tells more. It never holds your todos, habits or lists, pairing codes, tokens or secret keys.

Only the daemon, one per data dir, rotates the file (`rotate`), as processes rotating one
file at once would lose lines; the others reopen it once it was rotated.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler, WatchedFileHandler
from pathlib import Path

FILE = "kanso.log"
MAX_BYTES, BACKUPS = 1_000_000, 3


def path(data_dir: Path) -> Path:
    return data_dir / FILE


def short(key: str) -> str:
    """A device key as the log tells it: enough to tell devices apart."""
    return key[:10]


def setup(data_dir: Path, role: str, rotate: bool = False, stderr: bool = False):
    """Log this process, as `role`, to the data dir's log; with `stderr` there too, unless
    stderr is the log already, as a spawned daemon's is. Replaces an earlier setup."""
    file = path(data_dir)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(file, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        if rotate
        else WatchedFileHandler(file, encoding="utf-8")
    ]
    to_stderr = stderr and not writes_to(sys.stderr, file)
    if to_stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    formatter = logging.Formatter(f"%(asctime)s %(levelname)s {role}[%(process)d] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "kanso", False)]:
        root.removeHandler(handler)
        handler.close()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.kanso = True
        root.addHandler(handler)
    debug = os.environ.get("KANSO_LOG", "").lower() == "debug"
    logging.getLogger("kanso").setLevel(logging.DEBUG if debug else logging.INFO)
    printed = not to_stderr and not writes_to(sys.stderr, file)  # else the log shows it there already

    def crashed(*error):
        logging.getLogger("kanso").critical("Crashed", exc_info=error)
        if printed:
            sys.__excepthook__(*error)

    sys.excepthook = crashed


def writes_to(stream, file: Path) -> bool:
    """Whether `stream` writes to `file`."""
    try:
        return file.exists() and os.path.samestat(os.fstat(stream.fileno()), os.stat(file))
    except (AttributeError, OSError, ValueError):  # no file behind it, as under pytest
        return False
