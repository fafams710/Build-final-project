"""Signed outbound webhooks with bounded retries; never raises to callers."""
import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Signature-256"


def sign_body(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookDispatcher:
    def __init__(
        self,
        secret: str,
        max_attempts: int = 3,
        timeout: float = 5.0,
        retry_backoff: float = 0.5,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._secret = secret
        self._max_attempts = max(1, max_attempts)
        self._timeout = timeout
        self._retry_backoff = retry_backoff
        self._client = client
        self._tasks: set[asyncio.Task] = set()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def dispatch(self, url: str, payload: dict) -> asyncio.Task:
        """Fire-and-forget; returns the task (useful for tests)."""
        task = asyncio.create_task(self.deliver(url, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def deliver(self, url: str, payload: dict) -> bool:
        try:
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            headers = {
                "Content-Type": "application/json",
                SIGNATURE_HEADER: sign_body(body, self._secret),
            }
            for attempt in range(1, self._max_attempts + 1):
                try:
                    resp = await self._get_client().post(url, content=body, headers=headers)
                    if 200 <= resp.status_code < 300:
                        return True
                    log.warning("webhook non-2xx (%s) attempt %d", resp.status_code, attempt)
                except Exception as exc:
                    log.warning("webhook error %s attempt %d", type(exc).__name__, attempt)
                if attempt < self._max_attempts:
                    await asyncio.sleep(self._retry_backoff * (2 ** (attempt - 1)))
            log.error("webhook delivery failed after %d attempts", self._max_attempts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("webhook delivery crashed")
        return False

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None
