"""Single upstream Finnhub websocket client with reconnect + resubscribe."""
import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from .models import Tick

log = logging.getLogger(__name__)

OnTick = Callable[[Tick], Awaitable[None]]


def parse_message(raw: Any) -> list[Tick]:
    """Parse a Finnhub frame into ticks. Non-trade frames yield []."""
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(msg, dict) or msg.get("type") != "trade":
        return []
    ticks = []
    for item in msg.get("data") or []:
        try:
            ticks.append(
                Tick(
                    symbol=str(item["s"]),
                    price=float(item["p"]),
                    volume=float(item["v"]) if item.get("v") is not None else None,
                    ts_ms=int(item["t"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return ticks


class FinnhubClient:
    """Owns the one upstream connection. Created only by the app lifespan."""

    def __init__(
        self,
        url: str,
        api_key: str,
        on_tick: OnTick,
        get_symbols: Callable[[], list[str]],
        connect: Optional[Callable[[str], Any]] = None,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        self._url = f"{url}?token={api_key}"
        self._on_tick = on_tick
        self._get_symbols = get_symbols
        self._connect = connect
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._ws: Any = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="finnhub-upstream")

    async def stop(self) -> None:
        self._stopping = True
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                log.debug("error closing upstream", exc_info=True)
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def subscribe(self, symbol: str) -> None:
        await self._send("subscribe", symbol)

    async def unsubscribe(self, symbol: str) -> None:
        await self._send("unsubscribe", symbol)

    async def _send(self, kind: str, symbol: str) -> None:
        ws = self._ws
        if ws is None:
            return  # sent on (re)connect from the ref-counted symbol set
        await ws.send(json.dumps({"type": kind, "symbol": symbol}))

    async def _run(self) -> None:
        connect = self._connect
        if connect is None:
            import websockets

            connect = websockets.connect
        backoff = self._backoff_initial
        while not self._stopping:
            try:
                async with connect(self._url) as ws:
                    self._ws = ws
                    backoff = self._backoff_initial
                    for symbol in self._get_symbols():
                        await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                    async for raw in ws:
                        for tick in parse_message(raw):
                            try:
                                await self._on_tick(tick)
                            except Exception:
                                log.exception("tick handler failed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("upstream connection error: %s", type(exc).__name__)
            finally:
                self._ws = None
            if self._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._backoff_max)
