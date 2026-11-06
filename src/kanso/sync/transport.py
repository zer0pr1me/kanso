"""Authenticated, encrypted byte pipes between nodes identified by public keys.

A `Transport` dials and accepts peers by key and tells who is on the other end; a
`Channel` carries JSON messages over a pipe, each framed by its 4-byte length.
`IrohTransport` is the real network; `MemoryTransport` connects nodes in one process.
"""

import asyncio
import json
import struct
from abc import ABC, abstractmethod

import iroh

ALPN = b"kanso/sync/1"
MAX_MESSAGE = 64 * 1024 * 1024


class SyncError(Exception):
    """A peer can't be reached, broke the protocol, or refused us."""


class Pipe(ABC):
    @abstractmethod
    async def write(self, data: bytes): ...

    @abstractmethod
    async def read_exact(self, size: int) -> bytes:
        """Exactly `size` bytes; raises `SyncError` if the peer closed before sending them."""

    @abstractmethod
    async def close(self): ...


class Channel:
    """JSON messages over a pipe."""

    def __init__(self, pipe: Pipe):
        self.pipe = pipe

    async def send(self, message: dict):
        data = json.dumps(message, separators=(",", ":")).encode()
        if len(data) > MAX_MESSAGE:
            raise SyncError(f"Message of {len(data)} bytes is over the {MAX_MESSAGE} limit")
        await self.pipe.write(struct.pack(">I", len(data)) + data)

    async def receive(self) -> dict:
        (size,) = struct.unpack(">I", await self.pipe.read_exact(4))
        if size > MAX_MESSAGE:
            raise SyncError(f"Peer sent a message of {size} bytes, over the {MAX_MESSAGE} limit")
        try:
            message = json.loads(await self.pipe.read_exact(size))
        except ValueError as error:
            raise SyncError(f"Peer sent malformed JSON: {error}") from None
        if not isinstance(message, dict):
            raise SyncError("Peer sent a message that isn't an object")
        return message

    async def close(self):
        await self.pipe.close()


class StreamPipe(Pipe):
    """A pipe over an asyncio stream, as local connections are."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer

    async def write(self, data: bytes):
        try:
            self.writer.write(data)
            await self.writer.drain()
        except ConnectionError:
            raise SyncError("Connection lost") from None

    async def read_exact(self, size: int) -> bytes:
        try:
            return await self.reader.readexactly(size)
        except (asyncio.IncompleteReadError, ConnectionError):
            raise SyncError("Connection lost") from None

    async def close(self):
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except ConnectionError:
            pass


class Transport(ABC):
    key: str
    """This node's public key, which peers dial."""

    @abstractmethod
    async def connect(self, key: str) -> Channel:
        """A channel to the node with public key `key`; raises `SyncError` if unreachable."""

    @abstractmethod
    async def accept(self) -> tuple[str, Channel]:
        """The next incoming channel and the public key the peer proved it holds."""

    @abstractmethod
    async def close(self): ...


# In memory


class MemoryPipe(Pipe):
    def __init__(self, incoming: asyncio.Queue, outgoing: asyncio.Queue):
        self.incoming, self.outgoing = incoming, outgoing
        self.buffer = b""

    async def write(self, data: bytes):
        await self.outgoing.put(data)

    async def read_exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            if (chunk := await self.incoming.get()) is None:
                raise SyncError("Peer closed the connection")
            self.buffer += chunk
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    async def close(self):
        await self.outgoing.put(None)


class MemoryTransport(Transport):
    """Nodes in one process, found by key in the shared `network` dict."""

    def __init__(self, key: str, network: dict[str, "MemoryTransport"]):
        self.key, self.network = key, network
        self.incoming: asyncio.Queue[tuple[str, Channel] | None] = asyncio.Queue()
        network[key] = self

    async def connect(self, key: str) -> Channel:
        if not (peer := self.network.get(key)):
            raise SyncError(f"Can't reach {key}")
        there, back = asyncio.Queue(), asyncio.Queue()
        await peer.incoming.put((self.key, Channel(MemoryPipe(there, back))))
        return Channel(MemoryPipe(back, there))

    async def accept(self) -> tuple[str, Channel]:
        if not (incoming := await self.incoming.get()):
            raise SyncError("Endpoint closed")
        return incoming

    async def close(self):
        self.network.pop(self.key, None)
        await self.incoming.put(None)


# iroh


def reason(error: iroh.IrohError) -> str:
    """The first line of iroh's message, e.g. "No addressing information available"."""
    return error.message().splitlines()[0]


class IrohPipe(Pipe):
    def __init__(self, connection: iroh.Connection, stream: iroh.BiStream, dialer: bool):
        self.connection, self.dialer = connection, dialer
        self.send, self.recv = stream.send(), stream.recv()

    async def write(self, data: bytes):
        try:
            await self.send.write_all(data)
        except iroh.IrohError as error:
            raise SyncError(f"Connection lost: {reason(error)}") from None

    async def read_exact(self, size: int) -> bytes:
        try:
            return await self.recv.read_exact(size)
        except iroh.IrohError as error:
            raise SyncError(f"Connection lost: {reason(error)}") from None

    async def close(self):
        """Finish our side, then let the dialer close the connection once the acceptor has
        finished too, so neither side's last message is cut off."""
        try:
            await self.send.finish()
            await self.recv.read_to_end(MAX_MESSAGE)
            if self.dialer:
                self.connection.close(0, b"done")
            else:
                await asyncio.wait_for(self.connection.closed(), 5)
        except (iroh.IrohError, TimeoutError):
            pass


class IrohTransport(Transport):
    """iroh endpoint using n0's discovery and relays, so peers are reachable by key alone,
    behind NATs too; connections are QUIC, authenticated by the keys."""

    def __init__(self, endpoint: iroh.Endpoint):
        self.endpoint = endpoint
        self.key = str(endpoint.id())

    @classmethod
    async def start(cls, secret_key: bytes) -> "IrohTransport":
        iroh.iroh_ffi.uniffi_set_event_loop(asyncio.get_running_loop())
        options = iroh.EndpointOptions(preset=iroh.preset_n0(), secret_key=secret_key, alpns=[ALPN])
        return cls(await iroh.Endpoint.bind(options))

    @staticmethod
    def new_secret_key() -> bytes:
        return iroh.SecretKey.generate().to_bytes()

    @staticmethod
    def public_key(secret_key: bytes) -> str:
        return str(iroh.SecretKey.from_bytes(secret_key).public())

    async def connect(self, key: str) -> Channel:
        try:
            address = iroh.EndpointAddr(iroh.EndpointId.from_string(key), None, [])
            connection = await self.endpoint.connect(address, ALPN)
            stream = await connection.open_bi()
        except iroh.IrohError as error:
            raise SyncError(f"Can't reach {key}: {reason(error)}") from None
        return Channel(IrohPipe(connection, stream, dialer=True))

    async def accept(self) -> tuple[str, Channel]:
        while True:
            if not (incoming := await self.endpoint.accept_next()):
                raise SyncError("Endpoint closed")
            try:
                connection = await (await incoming.accept()).connect()
                stream = await connection.accept_bi()
            except iroh.IrohError:
                continue  # a peer that went away mid-handshake
            return str(connection.remote_id()), Channel(IrohPipe(connection, stream, dialer=False))

    async def close(self):
        await self.endpoint.close()
