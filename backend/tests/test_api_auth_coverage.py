"""T011: every /api route must carry require_api_key in its dependant tree, so a
router added without the key check (e.g. included outside the parent API router
in create_app) is caught here instead of shipping public."""
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.main import create_app
from app.security import require_api_key
from tests.fakes.fake_db import FakeDatabase
from tests.fakes.fake_finnhub import FakeFinnhubClient


def _app(settings, db=None):
    FakeFinnhubClient.instances.clear()
    return create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=db or FakeDatabase())


def _effective_routes(routes):
    """Flatten app.routes. Newer FastAPI keeps an include_router() call as a lazy
    _IncludedRouter node (expanded via effective_candidates()); older versions
    copy the routes straight into app.routes. Handle both."""
    for route in routes:
        if hasattr(route, "effective_candidates"):
            yield from _effective_routes(route.effective_candidates())
        else:
            yield route


def _resolve(route):
    """Return (path, dependant) for a flattened route; dependant is None when the
    route has none (plain Starlette Route/Mount)."""
    inner = getattr(route, "starlette_route", None) or route
    path = getattr(inner, "path", "") or getattr(route, "path", "")
    dependant = getattr(inner, "dependant", None) or getattr(route, "dependant", None)
    return path, dependant


def _has_key_check(dependant) -> bool:
    if dependant is None:
        return False
    if dependant.call is require_api_key:
        return True
    return any(_has_key_check(sub) for sub in dependant.dependencies)


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def walk_api_routes(app):
    """Return (inspected, offenders): paths of all /api routes, and those whose
    dependant tree lacks require_api_key."""
    inspected, offenders = [], []
    for route in _effective_routes(app.routes):
        path, dependant = _resolve(route)
        if not _is_api_path(path):
            continue
        inspected.append(path)
        if not _has_key_check(dependant):
            offenders.append(path)
    return inspected, offenders


def _dummy_router() -> APIRouter:
    router = APIRouter(prefix="/api/x")

    @router.get("")
    async def dummy():
        return {}

    return router


def _noop() -> None:
    return None


def test_real_app_every_api_route_protected(settings):
    inspected, offenders = walk_api_routes(_app(settings))
    assert offenders == []
    assert len(inspected) > 0
    for path in ("/api/rules", "/api/rules/{rule_id}", "/api/alerts", "/api/symbols"):
        assert path in inspected


def test_walker_flags_router_included_outside_parent(settings):
    app = _app(settings)
    app.include_router(_dummy_router())
    _, offenders = walk_api_routes(app)
    assert offenders == ["/api/x"]


def test_walker_flags_non_key_dependency(settings):
    app = _app(settings)
    app.include_router(_dummy_router(), dependencies=[Depends(_noop)])
    _, offenders = walk_api_routes(app)
    assert offenders == ["/api/x"]


def test_walker_accepts_protected_dummy(settings):
    app = _app(settings)
    app.include_router(_dummy_router(), dependencies=[Depends(require_api_key)])
    inspected, offenders = walk_api_routes(app)
    assert offenders == []
    assert "/api/x" in inspected


def test_walker_follows_nested_dependency(settings):
    def wrapper(_=Depends(require_api_key)):
        return None

    app = _app(settings)
    app.include_router(_dummy_router(), dependencies=[Depends(wrapper)])
    _, offenders = walk_api_routes(app)
    assert offenders == []


def test_open_routes_are_not_walked(settings):
    inspected, _ = walk_api_routes(_app(settings))
    for path in ("/health", "/openapi.json", "/docs", "/redoc", "/ws"):
        assert path not in inspected
    assert all(_is_api_path(p) for p in inspected)


def test_repeated_create_app_does_not_duplicate_routes(settings):
    first, _ = walk_api_routes(_app(settings))
    second, _ = walk_api_routes(_app(settings))
    third, _ = walk_api_routes(_app(settings))
    assert len(first) == len(second) == len(third) > 0


def test_docs_and_schema_open_without_key(settings, fake_db):
    with TestClient(_app(settings, fake_db)) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 200
