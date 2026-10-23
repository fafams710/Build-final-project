import asyncio
import json
import re
from pathlib import Path

import httpx

from app.webhooks import SIGNATURE_HEADER, WebhookDispatcher, sign_body

SECRET = "unit-test-secret"
PAYLOAD = {"event": "rule_triggered", "symbol": "AAPL", "price": 101.5}


def dispatcher(handler, attempts=3):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebhookDispatcher(SECRET, max_attempts=attempts, retry_backoff=0.001, client=client)


async def test_signature_header_valid_for_body():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.content
        seen["sig"] = request.headers.get(SIGNATURE_HEADER)
        return httpx.Response(200)

    d = dispatcher(handler)
    assert await d.deliver("http://hook.example/x", PAYLOAD) is True
    assert seen["sig"] == sign_body(seen["body"], SECRET)
    assert seen["sig"].startswith("sha256=")
    assert json.loads(seen["body"]) == PAYLOAD
    await d.close()


async def test_retries_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500 if len(calls) < 3 else 204)

    d = dispatcher(handler)
    assert await d.deliver("http://hook.example/x", PAYLOAD) is True
    assert len(calls) == 3
    await d.close()


async def test_failure_is_bounded_and_never_raises():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("unreachable")

    d = dispatcher(handler, attempts=2)
    assert await d.deliver("http://hook.example/x", PAYLOAD) is False
    assert len(calls) == 2
    await d.close()


async def test_dispatch_does_not_block_caller():
    gate = asyncio.Event()

    async def handler(request):
        await gate.wait()
        return httpx.Response(200)

    d = dispatcher(handler)
    task = d.dispatch("http://hook.example/x", PAYLOAD)  # returns immediately
    assert not task.done()
    gate.set()
    await task
    await d.close()


def test_no_secret_shaped_literals_in_source():
    root = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(
        r"""(api_key|secret|password|token)\w*\s*=\s*["'][^"']{8,}["']""", re.IGNORECASE
    )
    key_like = re.compile(r"""["'][A-Za-z0-9]{32,}["']""")
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text) or key_like.search(text):
            offenders.append(str(path))
    assert offenders == []
