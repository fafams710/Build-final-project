from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/symbols")


@router.get("")
async def list_symbols(request: Request) -> list[dict]:
    subs = request.app.state.services.subscriptions
    return [{"symbol": s, "subscribers": subs.ref_count(s)} for s in subs.active_symbols()]
