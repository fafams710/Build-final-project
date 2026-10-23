from fastapi import APIRouter, HTTPException, Request

from ..models import normalize_symbol

router = APIRouter(prefix="/api/symbols")


@router.get("")
async def list_symbols(request: Request) -> list[dict]:
    subs = request.app.state.services.subscriptions
    return [{"symbol": s, "subscribers": subs.ref_count(s)} for s in subs.active_symbols()]


@router.get("/{symbol}/price")
async def last_price(symbol: str, request: Request) -> dict:
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid symbol")
    cached = await request.app.state.services.cache.get_price(sym)
    if cached is None:
        raise HTTPException(status_code=404, detail="no price cached")
    return {"symbol": sym, **cached}
