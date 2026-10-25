"""T010: shared API key on /api/*, websocket Origin + key gate, startup check,
log masking."""
import io
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from tests.conftest import AUTH, TEST_KEY
from tests.fakes.fake_db import FakeDatabase
from tests.fakes.fake_finnhub import FakeFinnhubClient

GOOD_ORIGIN = "http://localhost:5173"
DETAIL = {"detail": "invalid or missing API key"}
RULE = {"symbol": "AAPL", "type": "price_above", "threshold": 100}


def _app(settings, db=None):
    FakeFinnhubClient.instances.clear()
    return create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=db or FakeDatabase())


@pytest.fixture
def raw(settings, fake_db):
    """TestClient with NO default key header."""
    app = _app(settings, fake_db)
    with TestClient(app) as c:
        yield c


# ---- config ------------------------------------------------------------------
def test_setting_default_and_env(monkeypatch):
    monkeypatch.delenv("APP_API_KEY", raising=False)
    assert Settings(_env_file=None).app_api_key == ""
    monkeypatch.setenv("APP_API_KEY", "abc")
    assert Settings(_env_file=None).app_api_key == "abc"


def test_env_example_documents_key():
    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    assert "\nAPP_API_KEY=\n" in text
    assert "secrets.token_urlsafe(32)" in text


# ---- startup -----------------------------------------------------------------
class _SpyDb(FakeDatabase):
    connects = 0

    async def connect(self) -> None:
        type(self).connects += 1


@pytest.mark.parametrize("key", ["", "   "])
@pytest.mark.parametrize("persist", [True, False])
def test_startup_fails_without_key(key, persist, caplog):
    _SpyDb.connects = 0
    db = _SpyDb()
    s = Settings(_env_file=None, finnhub_api_key="", database_url="", app_api_key=key,
                 rules_persistence_enabled=persist)
    app = _app(s, db)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError):
            with TestClient(app):
                pass
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("startup failed" in r.getMessage() and "APP_API_KEY" in r.getMessage() for r in errors)
    assert FakeFinnhubClient.instances == []
    assert _SpyDb.connects == 0
    assert db.close_calls == 0


def test_create_app_without_key_does_not_raise():
    create_app()
    create_app(Settings(_env_file=None, app_api_key=""))


# ---- REST ----------------------------------------------------------------------
CASES = [
    ("get", "/api/rules", None, 200),
    ("post", "/api/rules", RULE, 201),
    ("delete", "/api/rules/x", None, 404),
    ("get", "/api/alerts", None, 200),
    ("get", "/api/symbols", None, 200),
]


@pytest.mark.parametrize("method,path,body,ok_status", CASES)
def test_rest_requires_key(raw, method, path, body, ok_status):
    kw = {"json": body} if body is not None else {}
    call = getattr(raw, method)
    missing = call(path, **kw)
    wrong = call(path, headers={"X-API-Key": "nope"}, **kw)
    empty = call(path, headers={"X-API-Key": ""}, **kw)
    nonascii = call(path, headers={"X-API-Key": "café".encode("utf-8")}, **kw)
    for r in (missing, wrong, empty, nonascii):
        assert r.status_code == 401
        assert r.json() == DETAIL
    assert missing.content == wrong.content
    assert call(path, headers=AUTH, **kw).status_code == ok_status


def test_unauthenticated_invalid_body_is_401_not_422(raw):
    assert raw.post("/api/rules", json={"bogus": 1}).status_code == 401


def test_no_state_change_without_key(raw, fake_db):
    assert raw.post("/api/rules", json=RULE).status_code == 401
    assert fake_db.rules == {}
    assert fake_db.watchlist == set()
    assert raw.app.state.services.rule_store.for_symbol("AAPL") == []


def test_health_open(raw):
    r = raw.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "degraded", "database": "ok", "upstream": "disconnected"}


def test_preflight_without_key(raw):
    r = raw.options(
        "/api/rules",
        headers={
            "Origin": GOOD_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == GOOD_ORIGIN
    assert "x-api-key" in r.headers["access-control-allow-headers"].lower()


# ---- websocket -------------------------------------------------------------------
def _roundtrip(ws):
    ws.send_json({"action": "subscribe", "symbol": "AAPL"})
    assert ws.receive_json() == {"type": "subscribed", "symbol": "AAPL"}


def test_ws_accepts_good_key_without_origin(raw):
    with raw.websocket_connect(f"/ws?key={TEST_KEY}") as ws:
        _roundtrip(ws)


def test_ws_accepts_good_key_good_origin(raw):
    with raw.websocket_connect(f"/ws?key={TEST_KEY}", headers={"Origin": GOOD_ORIGIN}) as ws:
        _roundtrip(ws)


@pytest.mark.parametrize(
    "url,origin",
    [
        (f"/ws?key={TEST_KEY}", "http://evil.example"),
        (f"/ws?key={TEST_KEY}", "null"),
        ("/ws?key=wrong", GOOD_ORIGIN),
        ("/ws", GOOD_ORIGIN),
        ("/ws", None),
        ("/ws?key=wrong", None),
        ("/ws?key=", None),
        ("/ws?key=", GOOD_ORIGIN),
    ],
)
def test_ws_rejected(raw, url, origin):
    headers = {"Origin": origin} if origin is not None else {}
    with pytest.raises(WebSocketDisconnect) as exc:
        with raw.websocket_connect(url, headers=headers):
            pass
    assert exc.value.code == 1008
    svc = raw.app.state.services
    assert len(svc.connections) == 0
    assert svc.subscriptions.active_symbols() == []
    assert FakeFinnhubClient.instances[0].subscribed == []


def test_app_never_logs_key(raw, caplog):
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(WebSocketDisconnect):
            with raw.websocket_connect("/ws?key=wrong", headers={"Origin": "http://evil.example"}):
                pass
        with raw.websocket_connect(f"/ws?key={TEST_KEY}") as ws:
            _roundtrip(ws)
        assert raw.get("/api/rules").status_code == 401
        assert raw.get("/api/rules", headers=AUTH).status_code == 200
    assert TEST_KEY not in caplog.text


# ---- log masking -----------------------------------------------------------------
def _capture(logger_name):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger(logger_name)
    lg.addHandler(handler)
    return lg, handler, stream


def test_masking_filter_on_uvicorn_loggers():
    create_app(Settings(_env_file=None, app_api_key="SECRET+x"))
    access, h1, out1 = _capture("uvicorn.access")
    error, h2, out2 = _capture("uvicorn.error")
    old = (access.level, error.level)
    access.setLevel(logging.INFO)
    error.setLevel(logging.INFO)
    try:
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/ws?key=SECRET%2Bx&a=1", "1.1", 403)
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/ws?a=1&key=other", "1.1", 403)
        error.info('%s - "WebSocket %s" [accepted]', "127.0.0.1:1", "/ws?key=SECRET%2Bx")
        error.info("literal SECRET+x here")
    finally:
        access.removeHandler(h1)
        error.removeHandler(h2)
        access.setLevel(old[0])
        error.setLevel(old[1])
    text = out1.getvalue() + out2.getvalue()
    assert "SECRET" not in text and "other" not in text
    assert "key=***" in text and "&a=1" in text and "a=1&key=***" in text


def test_masking_filter_not_stacked_and_never_drops():
    from app.security import KeyMaskFilter

    s = Settings(_env_file=None, app_api_key="k")
    for _ in range(3):
        create_app(s)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        assert sum(isinstance(f, KeyMaskFilter) for f in lg.filters) <= 1
        for h in lg.handlers:
            assert sum(isinstance(f, KeyMaskFilter) for f in h.filters) <= 1
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "no secrets", None, None)
    assert KeyMaskFilter().filter(rec) is True
