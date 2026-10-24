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
from .models import Tick
from .routers import health, rules as rules_router, symbols, ws
from .rules import RuleEngine, RuleStore
from .subscription_manager import SubscriptionManager

log = logging.getLogger(__name__)

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
        self._tasks: set[asyncio.Task] = set()

        self.db = db or Database(
            settings.database_url,
            persist_rules=settings.rules_persistence_enabled,
            persist_ticks=settings.ticks_persistence_enabled,
        )
        self.subscriptions = SubscriptionManager(self._up_subscribe, self._up_unsubscribe)
        self.connections = ConnectionManager(on_disconnect=self.subscriptions.disconnect)
        self.rule_store = RuleStore(repo=self.db)
        self.engine = RuleEngine(self.rule_store)

    async def _up_subscribe(self, symbol: str) -> None:
        if self.upstream is not None:
            await self.upstream.subscribe(symbol)

    async def _up_unsubscribe(self, symbol: str) -> None:
        if self.upstream is not None:
            await self.upstream.unsubscribe(symbol)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
        self.upstream = factory(self.settings, self.on_tick, self.subscriptions.active_symbols)
        if self.upstream is not None:
            await self.upstream.start()

    async def shutdown(self) -> None:
        if self.upstream is not None:
            await self.upstream.stop()
        for task in list(self._tasks):
            task.cancel()
        await self.db.close()


def create_app(
    settings: Optional[Settings] = None,
    upstream_factory: Optional[UpstreamFactory] = None,
    db=None,
) -> FastAPI:
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
    app.include_router(ws.router)
    return app


app = create_app()
