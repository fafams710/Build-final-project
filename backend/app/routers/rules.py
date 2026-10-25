import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..db import WatchlistFull
from ..models import Rule, RuleCreate, normalize_symbol

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules")


def _unavailable(action: str, exc: Exception) -> HTTPException:
    # Log only the exception type: messages may embed connection details.
    log.error("rule %s failed: %s", action, type(exc).__name__)
    return HTTPException(status_code=503, detail="rule storage unavailable")


@router.post("", status_code=201, response_model=Rule)
async def create_rule(data: RuleCreate, request: Request) -> Rule:
    services = request.app.state.services
    rule = Rule(**data.model_dump())
    try:
        await services.db.create_rule_with_watchlist(
            rule, services.settings.watchlist_max_symbols
        )
    except WatchlistFull:
        raise HTTPException(status_code=409, detail="watchlist full")
    except Exception as exc:
        raise _unavailable("save", exc)
    services.rule_store.put(rule)
    await services.watch_symbol(rule.symbol)
    return rule


@router.get("", response_model=list[Rule])
async def list_rules(
    request: Request,
    symbol: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
) -> list[Rule]:
    if symbol:
        try:
            symbol = normalize_symbol(symbol)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid symbol")
    try:
        return await request.app.state.services.db.list_rules(symbol, limit)
    except Exception as exc:
        raise _unavailable("list", exc)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, request: Request) -> Response:
    services = request.app.state.services
    try:
        deleted = await services.db.delete_rule(rule_id)
    except Exception as exc:
        raise _unavailable("delete", exc)
    if not deleted:
        raise HTTPException(status_code=404, detail="rule not found")
    services.rule_store.discard(rule_id)
    return Response(status_code=204)
