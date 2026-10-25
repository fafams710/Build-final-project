import logging

from fastapi import APIRouter, HTTPException, Query, Request

from ..models import Alert

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts")


@router.get("", response_model=list[Alert])
async def list_alerts(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[Alert]:
    try:
        return await request.app.state.services.db.list_alerts(limit)
    except Exception as exc:
        # Log only the exception type: messages may embed connection details.
        log.error("alert list failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="alert storage unavailable")
