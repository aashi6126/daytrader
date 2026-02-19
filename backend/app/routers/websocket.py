from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.dependencies import get_ws_manager

router = APIRouter()


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    ws_manager = get_ws_manager()
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
