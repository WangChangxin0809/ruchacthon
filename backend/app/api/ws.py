"""§3.3: connect, get everything after `since_seq` replayed first, then
live events -- never rely on the client refreshing to fix a gap.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..store.events import get_event_bus

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, project_id: str, since_seq: int = 0):
    await websocket.accept()
    bus = get_event_bus()
    queue = bus.subscribe(project_id)
    try:
        for event in bus.since(project_id, since_seq):
            await websocket.send_json(event)
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(project_id, queue)
