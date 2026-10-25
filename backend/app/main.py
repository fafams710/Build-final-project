"""FastAPI app: lifespan owns the single upstream Finnhub connection."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .connection_manager import ConnectionManager
from . import db as db_module
from .db import Database
from .finnhub_client import FinnhubClient
from .models import Alert, Tick, normalize_symbol
from .routers import alerts as alerts_router, health, rules as rules_router, symbols, ws
from .rules import RuleEngine, RuleStore
from .security import install_key_mask, require_api_key
from .subscription_manager import SubscriptionManager

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Make INFO records from app.* visible under uvicorn/docker. Idempotent;
    adds a handler only if neither the app nor the root logger has one."""
    app_log = logging.getLogger("app")
    if app_log.level == logging.NOTSET or app_log.level > logging.INFO:
        app_log.setLevel(logging.INFO)
    if not app_log.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
        app_log.addHandler(handler)
        app_log.propagate = False  # do not print twice if root gains a handler later


# factory(settings, on_tick, get_symbols) -> object with async start/stop/subscribe/unsubscribe
UpstreamFactory = Callable[[Settings, Callable, Callable], Any]


def default_upstream_factory(settings: Settings, on_tick, get_symbols) -> Optional[FinnhubClient]:
    if not settings.finnhub_api_key:
        log.warning("FINNHUB_API_KEY not set; running without upstream market data")
        return None
    return FinnhubClient(
        url=settings.finnhub_ws_url,
        api_key=settings.finnhub_api_key,
        on_tick=on_tick,
        get_symbols=get_symbols,
        backoff_initial=settings.reconnect_initial_seconds,
        backoff_max=settings.reconnect_max_seconds,
    )


class Services:
    """Wires the components together; one instance per app."""

    def __init__(self, settings: Settings, db=None) -> None:
        self.settings = settings
        self.upstream: Any = None
        self.watchlist: set[str] = set()
        # Serializes every upstream subscribe/unsubscribe decision + call so the
        # last call reflects the latest state. Lock order: manager lock -> this.
        self._upstream_lock = asyncio.Lock()
        self._upstream_sent: set[str] = set()

        self.db = db or Database(
            settings.database_url,
            persist_rules=settings.rules_persistence_enabled,
            persist_ticks=settings.ticks_persistence_enabled,
        )
        self.subscriptions = SubscriptionManager(self._up_subscribe, self._up_unsubscribe)
        self.connections = ConnectionManager(on_disconnect=self.subscriptions.disconnect)
        self.rule_store = RuleStore(repo=self.db)
        self.engine = RuleEngine(self.rule_store)

    # Upstream set == watchlist UNION browser-held symbols. Every path (browser
    # subscribe/unsubscribe, rule creation) changes its own state and then calls
    # _reconcile, which compares what is wanted with what was actually sent
    # (_upstream_sent) under one lock. Nobody defers to anybody, so no
    # interleaving can leave a wanted symbol unsent or an unwanted one sent.
    # _upstream_sent semantics: a successful subscribe adds, a successful
    # unsubscribe removes; a failed subscribe is not recorded (retried by the
    # next reconcile of that symbol) and a failed unsubscribe keeps the symbol
    # recorded (so a failed unsubscribe is retried, and a re-want sends nothing).
    # On every (re)connect FinnhubClient resubscribes the wanted set via
    # _symbols_for_connect, which resets the sent set to exactly that set.
    def _wanted(self, symbol: str) -> bool:
        return symbol in self.watchlist or self.subscriptions.ref_count(symbol) > 0

    async def _reconcile(self, symbol: str) -> None:
        async with self._upstream_lock:
            if self.upstream is None:
                return
            wanted = self._wanted(symbol)
            sent = symbol in self._upstream_sent
            if wanted and not sent:
                try:
                    await self.upstream.subscribe(symbol)
                except Exception as exc:
                    log.warning("upstream subscribe failed for %s: %s", symbol, type(exc).__name__)
                else:
                    self._upstream_sent.add(symbol)
            elif sent and not wanted:
                try:
                    await self.upstream.unsubscribe(symbol)
                except Exception as exc:
                    log.warning("upstream unsubscribe failed for %s: %s", symbol, type(exc).__name__)
                else:
                    self._upstream_sent.discard(symbol)

    async def _up_subscribe(self, symbol: str) -> None:
        await self._reconcile(symbol)

    async def _up_unsubscribe(self, symbol: str) -> None:
        await self._reconcile(symbol)

    async def watch_symbol(self, symbol: str) -> None:
        """Rule path: mark the symbol watched and reconcile upstream (one
        subscribe if nothing sent it yet). Upstream failure is logged only."""
        if symbol in self.watchlist:
            return
        self.watchlist.add(symbol)
        await self._reconcile(symbol)

    def upstream_symbols(self) -> list[str]:
        return sorted(self.watchlist | set(self.subscriptions.active_symbols()))

    def _symbols_for_connect(self) -> list[str]:
        """get_symbols callable for the upstream client: it resubscribes this
        set on every (re)connect, so the sent set becomes exactly the wanted set."""
        wanted = self.upstream_symbols()
        self._upstream_sent = set(wanted)
        return wanted

    async def on_tick(self, tick: Tick) -> None:
        """Ingestion path. Everything before the broadcast is non-blocking."""
        try:
            self.db.enqueue_tick(tick)
            for rule, event in self.engine.evaluate(tick):
                log.info(
                    "rule fired: rule_id=%s symbol=%s type=%s threshold=%s price=%s",
                    event.get("rule_id"),
                    event.get("symbol"),
                    event.get("rule_type"),
                    event.get("threshold"),
                    event.get("price"),
                )
                self.db.enqueue_alert(
                    Alert(
                        rule_id=rule.id,
                        symbol=rule.symbol,
                        rule_type=event["rule_type"],
                        threshold=rule.threshold,
                        price=event["price"],
                        triggered_at=event["triggered_at"],
                    )
                )
        except Exception:
            log.exception("tick side-effects failed")
        try:
            await self.connections.send_to(
                self.subscriptions.subscribers(tick.symbol),
                {
                    "type": "tick",
                    "symbol": tick.symbol,
                    "price": tick.price,
                    "volume": tick.volume,
                    "timestamp": tick.ts_ms,
                },
            )
        except Exception:
            log.exception("broadcast failed")

    async def startup(self, factory: UpstreamFactory) -> None:
        try:
            await self._load_state()
        except Exception:
            # Fail loudly: release workers and pool, never start the upstream.
            await self.db.close()
            raise
        self.upstream = factory(self.settings, self.on_tick, self._symbols_for_connect)
        if self.upstream is not None:
            await self.upstream.start()

    async def _load_state(self) -> None:
        """Connect to Postgres and load rules and watchlist. With
        RULES_PERSISTENCE_ENABLED=true Postgres is required and any failure
        raises; with false it is the explicit opt-out (start empty, rule and
        alert endpoints answer 503)."""
        limit = db_module.DB_STARTUP_TIMEOUT_SECONDS
        try:
            await asyncio.wait_for(self._load_state_unbounded(), timeout=limit)
        except asyncio.TimeoutError:
            # Fixed text on purpose: no exception message, URL or credential.
            log.error(
                "startup failed: Postgres setup timed out (startup limit %gs); "
                "set RULES_PERSISTENCE_ENABLED=false to run without it",
                limit,
            )
            raise RuntimeError("Postgres setup timed out at startup")

    async def _load_state_unbounded(self) -> None:
        await self.db.connect()
        if not self.settings.rules_persistence_enabled:
            return
        if not self.db.available:
            if getattr(self.db, "last_error_timed_out", False):
                # Fixed text: no exception message, URL or credential.
                log.error(
                    "startup failed: Postgres setup timed out "
                    "(query/connect limit %gs); "
                    "set RULES_PERSISTENCE_ENABLED=false to run without it",
                    db_module.DB_COMMAND_TIMEOUT_SECONDS,
                )
                raise RuntimeError("Postgres setup timed out at startup")
            log.error(
                "startup failed: Postgres unavailable: %s; "
                "set RULES_PERSISTENCE_ENABLED=false to run without it",
                getattr(self.db, "last_error", None) or "unknown error",
            )
            raise RuntimeError("Postgres unavailable at startup")
        try:
            await self.rule_store.load()
            stored = await self.db.load_watchlist()
        except asyncio.TimeoutError:
            log.error(
                "startup failed: Postgres setup timed out "
                "(query/connect limit %gs); "
                "set RULES_PERSISTENCE_ENABLED=false to run without it",
                db_module.DB_COMMAND_TIMEOUT_SECONDS,
            )
            raise RuntimeError("Postgres setup timed out at startup")
        except Exception as exc:
            log.error(
                "startup failed: loading rules/watchlist: %s: %s; "
                "set RULES_PERSISTENCE_ENABLED=false to run without it",
                type(exc).__name__, exc,
            )
            raise
        watchlist: set[str] = set()
        for raw in stored:
            try:
                watchlist.add(normalize_symbol(raw))
            except ValueError:
                log.warning("skipping malformed watchlist symbol %r", raw)
        self.watchlist = watchlist

    async def shutdown(self) -> None:
        if self.upstream is not None:
            await self.upstream.stop()
        await self.db.close()


def create_app(
    settings: Optional[Settings] = None,
    upstream_factory: Optional[UpstreamFactory] = None,
    db=None,
) -> FastAPI:
    _configure_logging()
    settings = settings or get_settings()
    install_key_mask(settings.app_api_key)
    factory = upstream_factory or default_upstream_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # First: auth is required regardless of RULES_PERSISTENCE_ENABLED, and
        # nothing (db, workers, upstream) may start without it.
        if not settings.app_api_key.strip():
            log.error(
                "startup failed: APP_API_KEY is not set; generate one with "
                "python -c \"import secrets; print(secrets.token_urlsafe(32))\" "
                "and put it in .env"
            )
            raise RuntimeError("APP_API_KEY is not set")
        services = Services(settings, db=db)
        app.state.services = services
        await services.startup(factory)
        try:
            yield
        finally:
            await services.shutdown()

    app = FastAPI(title="Price & Stock Change Tracker", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    # Single parent for every /api router: it carries the key check. Add each
    # future /api router HERE, never on `app` (tests/test_api_auth_coverage.py
    # fails otherwise). /health and /ws are deliberately outside it. Built per
    # create_app call so repeated calls do not accumulate routes.
    api = APIRouter(dependencies=[Depends(require_api_key)])
    api.include_router(symbols.router)
    api.include_router(rules_router.router)
    api.include_router(alerts_router.router)
    app.include_router(api)
    app.include_router(ws.router)
    return app


app = create_app()
