"""Best-effort Redis cache of the last price per symbol."""
import json
import logging
from typing import Any, Optional

from .models import Tick

log = logging.getLogger(__name__)

KEY_PREFIX = "price:"


class PriceCache:
    """All operations swallow errors: Redis is only used for snapshots."""

    def __init__(self, url: str = "", client: Any = None) -> None:
        self._url = url
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None and self._url:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(
                self._url,
                socket_timeout=2,
                socket_connect_timeout=2,
                decode_responses=True,
            )
        return self._client

    async def set_price(self, tick: Tick) -> None:
        try:
            client = self._get_client()
            if client is None:
                return
            value = json.dumps({"price": tick.price, "ts_ms": tick.ts_ms, "volume": tick.volume})
            await client.set(KEY_PREFIX + tick.symbol, value)
        except Exception as exc:
            log.warning("redis set failed: %s", type(exc).__name__)

    async def get_price(self, symbol: str) -> Optional[dict]:
        try:
            client = self._get_client()
            if client is None:
                return None
            raw = await client.get(KEY_PREFIX + symbol)
            return json.loads(raw) if raw else None
        except Exception as exc:
            log.warning("redis get failed: %s", type(exc).__name__)
            return None

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None and hasattr(client, "aclose"):
            try:
                await client.aclose()
            except Exception:
                pass
