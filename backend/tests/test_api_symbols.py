from tests.fakes.fake_finnhub import FakeFinnhubClient


def tick_msg(ws):
    while True:
        m = ws.receive_json()
        if m["type"] == "tick":
            return m


def test_single_upstream_started_and_stopped_once(settings, fake_cache, webhooks):
    from fastapi.testclient import TestClient

    from app.main import create_app

    FakeFinnhubClient.instances.clear()
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory, cache=fake_cache, webhooks=webhooks)
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
            for ws in (a, b):
                for sym in ("AAPL", "MSFT"):
                    ws.send_json({"action": "subscribe", "symbol": sym})
                    assert ws.receive_json()["type"] == "subscribed"
        assert len(FakeFinnhubClient.instances) == 1
        assert FakeFinnhubClient.instances[0].start_calls == 1
        assert FakeFinnhubClient.instances[0].stop_calls == 0
    assert len(FakeFinnhubClient.instances) == 1
    assert FakeFinnhubClient.instances[0].stop_calls == 1


def test_subscribe_dedupes_upstream_and_fans_out_per_symbol(client):
    up = FakeFinnhubClient.instances[0]
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.send_json({"action": "subscribe", "symbol": "aapl"})
        assert a.receive_json() == {"type": "subscribed", "symbol": "AAPL"}
        b.send_json({"action": "subscribe", "symbol": "AAPL"})
        assert b.receive_json()["type"] == "subscribed"
        b.send_json({"action": "subscribe", "symbol": "AAPL"})  # idempotent
        assert b.receive_json()["type"] == "subscribed"
        assert up.subscribed == ["AAPL"]

        from app.models import Tick

        client.portal.call(up.on_tick, Tick(symbol="AAPL", price=10.0, ts_ms=1))
        assert tick_msg(a)["price"] == 10.0
        assert tick_msg(b)["price"] == 10.0

        b.send_json({"action": "unsubscribe", "symbol": "AAPL"})
        assert b.receive_json()["type"] == "unsubscribed"
        assert up.unsubscribed == []  # a still wants it

        client.portal.call(up.on_tick, Tick(symbol="AAPL", price=11.0, ts_ms=2))
        assert tick_msg(a)["price"] == 11.0

    # both gone without explicit unsubscribe from a -> zero-count unsubscribe
    for _ in range(100):
        if up.unsubscribed:
            break
        import time

        time.sleep(0.02)
    assert up.unsubscribed == ["AAPL"]
    assert client.get("/api/symbols").json() == []


def test_snapshot_from_cache_on_subscribe(client, fake_cache):
    fake_cache.prices["AAPL"] = {"price": 42.0, "ts_ms": 5, "volume": 1.0}
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "symbol": "AAPL"})
        assert ws.receive_json()["type"] == "subscribed"
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["price"] == 42.0
        # no cache entry: just subscribed, no error
        ws.send_json({"action": "subscribe", "symbol": "NEW"})
        assert ws.receive_json()["type"] == "subscribed"
        ws.send_json({"action": "bogus", "symbol": "NEW"})
        assert ws.receive_json()["type"] == "error"
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"


def test_tick_updates_cache_and_rule_fires_webhook(client, fake_cache, webhooks):
    from app.models import Tick

    up = FakeFinnhubClient.instances[0]
    r = client.post(
        "/api/rules",
        json={"symbol": "AAPL", "type": "price_above", "threshold": 100, "webhook_url": "http://h.example/x"},
    )
    assert r.status_code == 201
    client.portal.call(up.on_tick, Tick(symbol="AAPL", price=101.0, ts_ms=1))
    client.portal.call(up.on_tick, Tick(symbol="AAPL", price=102.0, ts_ms=2))
    assert len(webhooks.calls) == 1
    assert webhooks.calls[0][0] == "http://h.example/x"
    assert webhooks.calls[0][1]["rule_id"] == r.json()["id"]
    import time

    for _ in range(100):
        if fake_cache.prices.get("AAPL", {}).get("price") == 102.0:
            break
        time.sleep(0.02)
    assert fake_cache.prices["AAPL"]["price"] == 102.0
    assert client.get("/health").json() == {"status": "ok"}
