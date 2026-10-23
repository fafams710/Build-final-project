BASE = {"symbol": "aapl", "type": "price_above", "threshold": 100, "webhook_url": "http://h.example/x"}


def test_create_list_delete(client):
    r = client.post("/api/rules", json=BASE)
    assert r.status_code == 201
    rule = r.json()
    assert rule["symbol"] == "AAPL" and rule["id"]

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


def test_validation_rejects_bad_input(client):
    assert client.post("/api/rules", json={**BASE, "webhook_url": "ftp://x"}).status_code == 422
    assert client.post("/api/rules", json={**BASE, "threshold": -1}).status_code == 422
    assert client.post("/api/rules", json={**BASE, "type": "nope"}).status_code == 422
    assert client.post("/api/rules", json={**BASE, "symbol": "bad symbol!"}).status_code == 422
