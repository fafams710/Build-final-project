BASE = {"symbol": "aapl", "type": "price_above", "threshold": 100}


def test_create_list_delete(client):
    r = client.post("/api/rules", json=BASE)
    assert r.status_code == 201
    rule = r.json()
    assert rule["symbol"] == "AAPL" and rule["id"]
    keys = {"id", "symbol", "type", "threshold", "window_seconds", "created_at"}
    assert set(rule) == keys
    assert all(set(x) == keys for x in client.get("/api/rules").json())

    client.post("/api/rules", json={**BASE, "symbol": "MSFT", "type": "price_below"})
    assert len(client.get("/api/rules").json()) == 2
    assert [x["id"] for x in client.get("/api/rules", params={"symbol": "aapl"}).json()] == [rule["id"]]

    assert client.delete(f"/api/rules/{rule['id']}").status_code == 204
    assert client.delete(f"/api/rules/{rule['id']}").status_code == 404
    assert len(client.get("/api/rules").json()) == 1


def test_percent_rule_requires_window(client):
    bad = {**BASE, "type": "percent_change", "threshold": 5}
    assert client.post("/api/rules", json=bad).status_code == 422
    ok = {**bad, "window_seconds": 60}
    assert client.post("/api/rules", json=ok).status_code == 201


def _store(client):
    return client.app.state.services.rule_store


def test_create_returns_201_and_updates_memory(client, fake_db):
    r = client.post("/api/rules", json=BASE)
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"id", "symbol", "type", "threshold", "window_seconds", "created_at"}
    assert body["symbol"] == "AAPL"
    assert body["id"] in fake_db.rules and body["id"] in _store(client)


def test_create_503_when_db_fails_and_memory_unchanged(client, fake_db):
    fake_db.failing = True
    r = client.post("/api/rules", json=BASE)
    assert r.status_code == 503
    assert _store(client).for_symbol("AAPL") == []
    fake_db.failing = False
    assert client.get("/api/rules").json() == []


def test_list_orders_newest_first_and_honours_limit(client, fake_db):
    ids = []
    for i in range(3):
        rid = client.post("/api/rules", json={**BASE, "threshold": 10 + i}).json()["id"]
        fake_db.rules[rid].created_at = 1000.0 + i
        ids.append(rid)
    assert [x["id"] for x in client.get("/api/rules").json()] == ids[::-1]
    assert [x["id"] for x in client.get("/api/rules", params={"limit": 1}).json()] == [ids[2]]
    assert client.get("/api/rules", params={"limit": 500}).status_code == 200


def test_list_validation(client):
    for bad in ("0", "501", "abc"):
        assert client.get("/api/rules", params={"limit": bad}).status_code == 422
    assert client.get("/api/rules", params={"symbol": "bad symbol!"}).status_code == 422


def test_list_symbol_filter_is_normalized(client):
    client.post("/api/rules", json=BASE)
    client.post("/api/rules", json={**BASE, "symbol": "MSFT"})
    got = client.get("/api/rules", params={"symbol": " msft "}).json()
    assert [x["symbol"] for x in got] == ["MSFT"]


def test_list_reads_db_not_memory(client):
    from app.models import Rule

    _store(client).put(Rule(symbol="AAPL", type="price_above", threshold=1))
    assert client.get("/api/rules").json() == []


def test_list_503_when_db_fails(client, fake_db):
    fake_db.failing = True
    assert client.get("/api/rules").status_code == 503


def test_delete_503_when_db_fails_and_memory_unchanged(client, fake_db):
    rid = client.post("/api/rules", json=BASE).json()["id"]
    fake_db.failing = True
    assert client.delete(f"/api/rules/{rid}").status_code == 503
    assert rid in _store(client)
    fake_db.failing = False
    assert client.delete(f"/api/rules/{rid}").status_code == 204
    assert rid not in _store(client)


def test_delete_unknown_is_404(client):
    assert client.delete("/api/rules/nope").status_code == 404


def test_no_db_rules_503_health_200_and_ticks_still_evaluated(client_no_db):
    from app.models import Rule, Tick

    assert client_no_db.post("/api/rules", json=BASE).status_code == 503
    assert client_no_db.get("/api/rules").status_code == 503
    assert client_no_db.delete("/api/rules/x").status_code == 503
    assert client_no_db.get("/health").status_code == 200
    services = client_no_db.app.state.services
    assert services.rule_store.for_symbol("AAPL") == []
    services.rule_store.put(Rule(symbol="AAPL", type="price_above", threshold=1))
    fired = services.engine.evaluate(Tick(symbol="AAPL", price=5.0, ts_ms=1))
    assert len(fired) == 1
    client_no_db.portal.call(services.on_tick, Tick(symbol="AAPL", price=6.0, ts_ms=2))


def test_503_body_leaks_nothing(client_no_db):
    body = client_no_db.post("/api/rules", json=BASE).text
    assert "postgres" not in body.lower() and "password" not in body.lower()


def test_validation_rejects_bad_input(client):
    assert client.post("/api/rules", json={**BASE, "threshold": -1}).status_code == 422
    assert client.post("/api/rules", json={**BASE, "type": "nope"}).status_code == 422
    assert client.post("/api/rules", json={**BASE, "symbol": "bad symbol!"}).status_code == 422
