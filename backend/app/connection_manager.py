"""Tracks connected browser WebSockets and delivers messages to them."""
import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable, Iterable, Optional

log = logging.getLogger(__name__)

DisconnectCb = Callable[[str], Awaitable[None]]


class ConnectionManager:
    def __init__(
        self,
        on_disconnect: Optional[DisconnectCb] = None,
        send_timeout: float = 5.0,
    ) -> None:
        self._conns: dict[str, Any] = {}
        self._on_disconnect = on_disconnect
        self._send_timeout = send_timeout

    async def connect(self, websocket: Any) -> str:
        await websocket.accept()
        conn_id = uuid.uuid4().hex
        self._conns[conn_id] = websocket
        return conn_id

    async def disconnect(self, conn_id: str) -> None:
        """Idempotent: drops tracking and fires the disconnect callback once."""
        if self._conns.pop(conn_id, None) is None:
            return
        if self._on_disconnect is not None:
            try:
                await self._on_disconnect(conn_id)
            except Exception:
                log.exception("on_disconnect callback failed")

    @property
    def connection_ids(self) -> list[str]:
        return list(self._conns)

    def __len__(self) -> int:
        return len(self._conns)

    async def _send_one(self, conn_id: str, message: dict) -> None:
        ws = self._conns.get(conn_id)
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.send_json(message), self._send_timeout)
        except Exception as exc:  # includes disconnects and timeouts
            log.info("send to %s failed (%s); dropping connection", conn_id, type(exc).__name__)
            await self.disconnect(conn_id)

    async def send_to(self, conn_ids: Iterable[str], message: dict) -> None:
        ids = list(conn_ids)
        if ids:
            await asyncio.gather(*(self._send_one(c, message) for c in ids))

    async def broadcast(self, message: dict) -> None:
        await self.send_to(self.connection_ids, message)

    async def broadcast_symbol(self, subscriptions: Any, symbol: str, message: dict) -> None:
        """Send to only the connections subscribed to `symbol` (assumption A1)."""
        await self.send_to(subscriptions.subscribers(symbol), message)
