from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response

from ..models import Rule, RuleCreate, normalize_symbol

router = APIRouter(prefix="/api/rules")


@router.post("", status_code=201, response_model=Rule)
async def create_rule(data: RuleCreate, request: Request) -> Rule:
    return await request.app.state.services.rule_store.add(data)


@router.get("", response_model=list[Rule])
async def list_rules(request: Request, symbol: Optional[str] = None) -> list[Rule]:
    if symbol:
        try:
            symbol = normalize_symbol(symbol)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid symbol")
    return request.app.state.services.rule_store.list(symbol)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, request: Request) -> Response:
    if not await request.app.state.services.rule_store.remove(rule_id):
        raise HTTPException(status_code=404, detail="rule not found")
    return Response(status_code=204)
