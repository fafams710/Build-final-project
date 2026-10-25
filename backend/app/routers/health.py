import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import db as db_module

log = logging.getLogger(__name__)

router = APIRouter()


async def _database_state(services) -> str:
    # "disabled" only for the explicit opt-out; persistence required but
    # unusable (no pool, failed or slow probe) is "down".
    if not services.settings.rules_persistence_enabled:
        return "disabled"
    try:
        ok = await asyncio.wait_for(
            services.db.ping(), timeout=db_module.HEALTH_DB_TIMEOUT_SECONDS
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("health database probe failed: %s", type(exc).__name__)
        return "down"
    return "ok" if ok else "down"


def _upstream_state(services) -> str:
    upstream = services.upstream
    if upstream is None:
        return "disabled"
    return "connected" if getattr(upstream, "connected", False) is True else "disconnected"


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    services = request.app.state.services
    database = await _database_state(services)
    upstream = _upstream_state(services)
    healthy = database in ("ok", "disabled") and upstream in ("connected", "disabled")
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "database": database, "upstream": upstream},
        status_code=503 if database == "down" else 200,
    )
