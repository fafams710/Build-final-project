"""Offline stand-ins for Finnhub: a fake websocket connector and a fake client."""
import asyncio
import json

_DROP = object()
_CLOSE = object()


class FakeConnection:
    """Mimics a websockets client connection (async CM + async iterator)."""

    def __init__(self, upstream: "FakeUpstream") -> None:
        self.upstream = upstream
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    async def send(self, data: str) -> None:
        self.upstream.sent.append(json.loads(data))

    async def close(self) -> None:
        self.closed = True
        self.queue.put_nowait(_CLOSE)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is _DROP:
            raise ConnectionError("dropped")
        if item is _CLOSE:
            raise StopAsyncIteration
        return item


class FakeUpstream:
    """Callable used as FinnhubClient(connect=...). Records everything sent."""

    def __init__(self) -> None:
        self.connect_count = 0
        self.connections: list[FakeConnection] = []
        self.sent: list[dict] = []
        self.urls: list[str] = []

    def __call__(self, url: str) -> FakeConnection:
        self.connect_count += 1
        self.urls.append(url)
        conn = FakeConnection(self)
        self.connections.append(conn)
        return conn

    @property
    def current(self) -> FakeConnection:
        return self.connections[-1]

    def emit_trade(self, symbol: str, price: float, ts_ms: int = 1_700_000_000_000, volume: float = 1.0):
        frame = {"type": "trade", "data": [{"s": symbol, "p": price, "t": ts_ms, "v": volume}]}
        self.current.queue.put_nowait(json.dumps(frame))

    def drop(self) -> None:
        self.current.queue.put_nowait(_DROP)


class FakeFinnhubClient:
    """Injectable replacement for FinnhubClient in app-level tests."""

    instances: list["FakeFinnhubClient"] = []

    def __init__(self, on_tick, get_symbols) -> None:
        self.on_tick = on_tick
        self.get_symbols = get_symbols
        self.start_calls = 0
        self.stop_calls = 0
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        FakeFinnhubClient.instances.append(self)

    @classmethod
    def factory(cls, settings, on_tick, get_symbols) -> "FakeFinnhubClient":
        return cls(on_tick, get_symbols)

    async def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1

    async def subscribe(self, symbol: str) -> None:
        self.subscribed.append(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self.unsubscribed.append(symbol)
