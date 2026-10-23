"""Browser WebSocket: JSON control messages {action, symbol}."""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..models import normalize_symbol

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    svc = websocket.app.state.services
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
                snapshot = await svc.cache.get_price(symbol)
                if snapshot:
                    await reply(
                        {
                            "type": "snapshot",
                            "symbol": symbol,
                            "price": snapshot.get("price"),
                            "volume": snapshot.get("volume"),
                            "timestamp": snapshot.get("ts_ms"),
                        }
                    )
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
