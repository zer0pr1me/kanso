"""kanso: a local-first todo app that syncs between your devices."""

import hashlib
from functools import cache
from importlib.metadata import version
from pathlib import Path

__version__ = version("kanso")
"""This kanso's version, from `pyproject.toml` (see CHANGELOG.md)."""


@cache
def build() -> str:
    """This kanso's exact code, as a hash of its package's files: tells apart builds of one
    version too, such as a dev checkout and the installed release."""
    return code_hash(Path(__file__).parent)


def code_hash(package: Path) -> str:
    """A hash of the Python files under `package`, by their paths in it and contents."""
    digest = hashlib.sha256()
    for file in sorted(package.rglob("*.py")):
        digest.update(f"{file.relative_to(package).as_posix()}\0".encode() + file.read_bytes())
    return digest.hexdigest()[:16]
