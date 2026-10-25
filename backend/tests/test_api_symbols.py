from tests.conftest import AUTH, TEST_KEY
from tests.fakes.fake_finnhub import FakeFinnhubClient

WS = f"/ws?key={TEST_KEY}"


def tick_msg(ws):
    while True:
        m = ws.receive_json()
        if m["type"] == "tick":
            return m


def test_single_upstream_started_and_stopped_once(settings, fake_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    FakeFinnhubClient.instances.clear()
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=fake_db)
    with TestClient(app, headers=AUTH) as c:
        with c.websocket_connect(WS) as a, c.websocket_connect(WS) as b:
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
    with client.websocket_connect(WS) as a, client.websocket_connect(WS) as b:
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


def test_subscribe_without_snapshot_and_error_replies(client):
    with client.websocket_connect(WS) as ws:
        ws.send_json({"action": "subscribe", "symbol": "NEW"})
        assert ws.receive_json() == {"type": "subscribed", "symbol": "NEW"}
        ws.send_json({"action": "bogus", "symbol": "NEW"})
        assert ws.receive_json()["type"] == "error"
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"


def test_rule_fires_one_info_log_and_tick_still_broadcast(client, caplog):
    import logging

    from app.models import Tick

    up = FakeFinnhubClient.instances[0]
    r = client.post("/api/rules", json={"symbol": "AAPL", "type": "price_above", "threshold": 100})
    assert r.status_code == 201
    with client.websocket_connect(WS) as ws:
        ws.send_json({"action": "subscribe", "symbol": "AAPL"})
        assert ws.receive_json()["type"] == "subscribed"
        with caplog.at_level(logging.INFO, logger="app.main"):
            client.portal.call(up.on_tick, Tick(symbol="AAPL", price=101.0, ts_ms=1))
            client.portal.call(up.on_tick, Tick(symbol="AAPL", price=102.0, ts_ms=2))
        assert tick_msg(ws)["price"] == 101.0
        assert tick_msg(ws)["price"] == 102.0
    fired = [x for x in caplog.records if x.levelno == logging.INFO and "rule fired" in x.getMessage()]
    assert len(fired) == 1
    msg = fired[0].getMessage()
    for part in (r.json()["id"], "AAPL", "price_above", "100", "101.0"):
        assert part in msg
