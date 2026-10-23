"""Ref-counted symbol subscriptions across browser connections."""
import asyncio
import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

UpstreamFn = Callable[[str], Awaitable[None]]


class SubscriptionManager:
    """Tracks which connections want which symbols.

    The ref-count of a symbol is the number of distinct connections subscribed
    to it, so it can never drift or go negative. Upstream subscribe is sent on
    0 -> 1 and upstream unsubscribe on 1 -> 0 only.
    """

    def __init__(
        self,
        upstream_subscribe: Optional[UpstreamFn] = None,
        upstream_unsubscribe: Optional[UpstreamFn] = None,
    ) -> None:
        self._up_sub = upstream_subscribe
        self._up_unsub = upstream_unsubscribe
        self._by_symbol: dict[str, set[str]] = {}
        self._by_conn: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def _call(self, fn: Optional[UpstreamFn], symbol: str) -> None:
        if fn is None:
            return
        try:
            await fn(symbol)
        except Exception:
            log.exception("upstream call failed for %s", symbol)

    async def subscribe(self, conn_id: str, symbol: str) -> bool:
        """Returns True if this connection was newly subscribed."""
        async with self._lock:
            conns = self._by_symbol.setdefault(symbol, set())
            if conn_id in conns:
                return False
            first = not conns
            conns.add(conn_id)
            self._by_conn.setdefault(conn_id, set()).add(symbol)
            if first:
                await self._call(self._up_sub, symbol)
            return True

    async def unsubscribe(self, conn_id: str, symbol: str) -> bool:
        async with self._lock:
            return await self._remove(conn_id, symbol)

    async def disconnect(self, conn_id: str) -> None:
        async with self._lock:
            for symbol in list(self._by_conn.get(conn_id, ())):
                await self._remove(conn_id, symbol)
            self._by_conn.pop(conn_id, None)

    async def _remove(self, conn_id: str, symbol: str) -> bool:
        conns = self._by_symbol.get(symbol)
        if not conns or conn_id not in conns:
            return False
        conns.discard(conn_id)
        mine = self._by_conn.get(conn_id)
        if mine is not None:
            mine.discard(symbol)
            if not mine:
                self._by_conn.pop(conn_id, None)
        if not conns:
            del self._by_symbol[symbol]
            await self._call(self._up_unsub, symbol)
        return True

    def subscribers(self, symbol: str) -> set[str]:
        return set(self._by_symbol.get(symbol, ()))

    def ref_count(self, symbol: str) -> int:
        return len(self._by_symbol.get(symbol, ()))

    def active_symbols(self) -> list[str]:
        return sorted(self._by_symbol)

    def symbols_for(self, conn_id: str) -> set[str]:
        return set(self._by_conn.get(conn_id, ()))
