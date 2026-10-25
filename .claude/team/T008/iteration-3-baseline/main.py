"""FastAPI app: lifespan owns the single upstream Finnhub connection."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .connection_manager import ConnectionManager
from .db import Database
from .finnhub_client import FinnhubClient
from .models import Alert, Tick, normalize_symbol
from .routers import alerts as alerts_router, health, rules as rules_router, symbols, ws
from .rules import RuleEngine, RuleStore
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

        self.db = db or Database(
            settings.database_url,
            persist_rules=settings.rules_persistence_enabled,
            persist_ticks=settings.ticks_persistence_enabled,
        )
        self.subscriptions = SubscriptionManager(self._up_subscribe, self._up_unsubscribe)
        self.connections = ConnectionManager(on_disconnect=self.subscriptions.disconnect)
        self.rule_store = RuleStore(repo=self.db)
        self.engine = RuleEngine(self.rule_store)

    # Upstream set == watchlist UNION browser-held symbols. The browser path
    # defers to the watchlist (a watched symbol is subscribed by the rule path
    # or at connect); the rule path defers to browsers that already hold it.
    # Neither decision has an await between its check and the state it reads.
    async def _up_subscribe(self, symbol: str) -> None:
        if symbol in self.watchlist:  # fast path: do not queue behind the rule path
            return
        async with self._upstream_lock:
            if symbol in self.watchlist:
                return
            if self.upstream is not None:
                await self.upstream.subscribe(symbol)

    async def _up_unsubscribe(self, symbol: str) -> None:
        async with self._upstream_lock:
            if symbol in self.watchlist or self.subscriptions.ref_count(symbol) > 0:
                return
            if self.upstream is not None:
                await self.upstream.unsubscribe(symbol)

    async def watch_symbol(self, symbol: str) -> None:
        """Rule path: mark the symbol watched; subscribe upstream once if new
        and no browser already holds it. Upstream failure is logged only."""
        if symbol in self.watchlist:
            return
        self.watchlist.add(symbol)
        # Fast path: a browser already holds it upstream, nothing to send (and do
        # not queue behind that browser's in-flight subscribe).
        if self.upstream is None or self.subscriptions.ref_count(symbol) > 0:
            return
        async with self._upstream_lock:
            if self.subscriptions.ref_count(symbol) > 0:
                return
            try:
                await self.upstream.subscribe(symbol)
            except Exception as exc:
                log.warning("upstream subscribe failed for %s: %s", symbol, type(exc).__name__)

    def upstream_symbols(self) -> list[str]:
        return sorted(self.watchlist | set(self.subscriptions.active_symbols()))

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
        await self.db.connect()
        await self.rule_store.load()
        try:
            self.watchlist = {normalize_symbol(s) for s in await self.db.load_watchlist()}
        except Exception as exc:
            log.warning("loading watchlist failed: %s", type(exc).__name__)
            self.watchlist = set()
        self.upstream = factory(self.settings, self.on_tick, self.upstream_symbols)
        if self.upstream is not None:
            await self.upstream.start()

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
    factory = upstream_factory or default_upstream_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI):
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
    app.include_router(symbols.router)
    app.include_router(rules_router.router)
    app.include_router(alerts_router.router)
    app.include_router(ws.router)
    return app


app = create_app()
