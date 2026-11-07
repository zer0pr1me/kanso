"""One sync session between two nodes, over a `Channel` (`transport.py`).

    dialer                                    acceptor
    hello {protocol, schema, name, proof?}  ->
                                            <-  welcome {protocol, schema, name} | refuse {reason}
    digest {digest}               ->
                                  <-  rows {schema, digest, rows}
    rows {schema, rows}           ->

Hello and welcome keep this shape in every version of kanso, so peers always learn each
other's version (`protocol` defaults to 1, as the first releases didn't send it). The
session after them changes only with `PROTOCOL`: peers whose protocols differ end it there,
and the one with the older version needs an update. Peers of different schemas do sync: rows
of an older schema are upgraded (`migrations.upgrade`) and merged, rows of a newer one are
skipped, so the newer peer still gets the older one's changes. The transport's ALPN never
changes.

Strict turn-taking, so neither side can block writing while the other does too. The
acceptor talks to devices of its network, which the transport has already authenticated
by key, or to a new node proving it was given this node's pairing code; pairing makes
each the other's device, and since devices are synced rows, both networks become one.
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from kanso.core.migrations import current_version
from kanso.core.store import Store
from kanso.core.sync import wanted
from kanso.sync.transport import Channel, SyncError, Transport

PROTOCOL = 1
"""The version of the session after hello and welcome: bump it on any change to it that
peers of the current version can't follow."""


def version() -> tuple[int, int]:
    """This kanso's version, as peers compare it: protocol, then schema."""
    return PROTOCOL, current_version()


CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no 0/O, 1/I/L or U
CODE_LENGTH = 10
CODE_TTL = 10 * 60
CODE_ATTEMPTS = 3
SESSION_TIMEOUT = 60


# Pairing codes, kept in local meta so any kanso process can hand one out


def new_code(store: Store) -> str:
    """A fresh single-use code, replacing any earlier one, as `ABCDE-FGHJK`."""
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    store.set_meta("pair_code", json.dumps({"code": code, "expires": time.time() + CODE_TTL, "failures": 0}))
    return f"{code[:5]}-{code[5:]}"


def normalize(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def proof(code: str, dialer: str, acceptor: str) -> str:
    """Shows knowledge of `code`, bound to both keys so it can't be replayed to another node."""
    message = f"kanso-pair|{dialer}|{acceptor}".encode()
    return hmac.new(normalize(code).encode(), message, hashlib.sha256).hexdigest()


def check_proof(store: Store, given: str, dialer: str, acceptor: str) -> bool:
    """Whether `given` proves the current code; a match uses it up, and so do too many misses."""
    if not (saved := store.meta("pair_code")):
        return False
    pending = json.loads(saved)
    if time.time() > pending["expires"]:
        store.set_meta("pair_code", None)
        return False
    if hmac.compare_digest(given, proof(pending["code"], dialer, acceptor)):
        store.set_meta("pair_code", None)
        return True
    pending["failures"] += 1
    store.set_meta("pair_code", json.dumps(pending) if pending["failures"] < CODE_ATTEMPTS else None)
    return False


# Sessions


@dataclass
class Result:
    peer: str
    """The peer's name."""
    changed: int = 0
    """Rows our side merged."""
    version: tuple[int, int] = field(default_factory=version)
    """The peer's `version`."""

    @property
    def newer(self) -> bool:
        """The peer runs a newer kanso, so we couldn't take its changes: we need an update."""
        return self.version > version()

    @property
    def older(self) -> bool:
        """The peer runs an older kanso, so it couldn't take ours: it needs an update."""
        return self.version < version()


async def receive(channel: Channel, kind: str, **fields: type) -> dict:
    """The next message, which must be of `kind` with `fields` of those types; a refusal raises."""
    message = await channel.receive()
    if message.get("type") == "refuse":
        raise SyncError(f"Refused: {message.get('reason')}")
    if message.get("type") != kind or not all(isinstance(message.get(f), t) for f, t in fields.items()):
        raise SyncError(f"Peer broke the protocol: expected {kind}, got {str(message)[:80]}")
    return message


def peer_version(message: dict) -> tuple[int, int]:
    """The version a hello or welcome gives."""
    if not isinstance(protocol := message.get("protocol", 1), int):
        raise SyncError(f"Peer broke the protocol: protocol {str(protocol)[:20]}")
    return protocol, message["schema"]


def merge(store: Store, message: dict) -> int:
    """Merge a `rows` message, unless it's of a newer schema: changed rows."""
    if message["schema"] > current_version():
        return 0
    return store.merge_rows(message["rows"], message["schema"])


async def dial(store: Store, transport: Transport, key: str, name: str, code: str | None = None) -> Result:
    """Sync with the node `key`; with its pairing `code`, join its network first."""
    async with session():
        channel = await transport.connect(key)
        try:
            given = proof(code, transport.key, key) if code else None
            await channel.send({"type": "hello", "protocol": PROTOCOL, "schema": current_version(), "name": name, "proof": given})
            welcome = await receive(channel, "welcome", schema=int, name=str)
            peer = peer_version(welcome)
            if code:
                store.add_device(key, welcome["name"])
            if peer[0] != PROTOCOL:
                return Result(welcome["name"], 0, peer)
            mine = store.digest()
            await channel.send({"type": "digest", "digest": mine})
            reply = await receive(channel, "rows", schema=int, digest=dict, rows=dict)
            await channel.send({"type": "rows", "schema": current_version(), "rows": store.export(wanted(mine, reply["digest"]))})
            return Result(welcome["name"], merge(store, reply), peer)
        finally:
            await channel.close()


async def answer(store: Store, channel: Channel, key: str, own_key: str, name: str) -> Result:
    """Serve a node that dialed us, which proved it holds `key`."""
    async with session():
        try:
            hello = await receive(channel, "hello", schema=int, name=str)
            peer = peer_version(hello)
            if not store.is_trusted(key):
                if not isinstance(given := hello.get("proof"), str):
                    await refuse(channel, "Not paired: connect with this node's code")
                if not check_proof(store, given, key, own_key):
                    await refuse(channel, "Wrong or expired code")
                store.add_device(key, hello["name"])
            await channel.send({"type": "welcome", "protocol": PROTOCOL, "schema": current_version(), "name": name})
            if peer[0] != PROTOCOL:
                return Result(hello["name"], 0, peer)
            theirs = await receive(channel, "digest", digest=dict)
            mine = store.digest()
            rows = store.export(wanted(mine, theirs["digest"]))
            await channel.send({"type": "rows", "schema": current_version(), "digest": mine, "rows": rows})
            return Result(hello["name"], merge(store, await receive(channel, "rows", schema=int, rows=dict)), peer)
        finally:
            await channel.close()


async def refuse(channel: Channel, reason: str):
    await channel.send({"type": "refuse", "reason": reason})
    raise SyncError(f"Refused peer: {reason}")


@asynccontextmanager
async def session():
    """Bounds a session's time; a timeout, or data the store refuses, becomes a `SyncError`."""
    try:
        async with asyncio.timeout(SESSION_TIMEOUT):
            yield
    except TimeoutError:
        raise SyncError("Peer took too long") from None
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise SyncError(f"Peer sent bad data: {error}") from None
