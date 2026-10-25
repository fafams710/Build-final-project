"""Browser WebSocket: JSON control messages {action, symbol}."""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..models import normalize_symbol
from ..security import key_matches

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    svc = websocket.app.state.services
    # Gate before accept(): the handshake is answered with HTTP 403 and nothing
    # is registered. Same outcome for every failure reason.
    origin = websocket.headers.get("origin")
    if (origin is not None and origin not in svc.settings.cors_origin_list) or not key_matches(
        websocket.query_params.get("key"), svc.settings.app_api_key
    ):
        await websocket.close(code=1008)
        return
    conn_id = await svc.connections.connect(websocket)

    async def reply(message: dict) -> None:
        await svc.connections.send_to([conn_id], message)

    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
                action = msg["action"]
                symbol = normalize_symbol(msg["symbol"])
            except (ValueError, KeyError, TypeError):
                await reply({"type": "error", "message": "expected {action, symbol}"})
                continue

            if action == "subscribe":
                await svc.subscriptions.subscribe(conn_id, symbol)
                await reply({"type": "subscribed", "symbol": symbol})
            elif action == "unsubscribe":
                await svc.subscriptions.unsubscribe(conn_id, symbol)
                await reply({"type": "unsubscribed", "symbol": symbol})
            else:
                await reply({"type": "error", "message": "unknown action"})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket handler error")
    finally:
        await svc.connections.disconnect(conn_id)
