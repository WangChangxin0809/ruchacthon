"""App assembly only -- routes live in api/, execution in exec/, state in
store/. The AgentRoom MCP server is mounted in the same process so it
shares the one SQLite-backed RoomState with the dashboard (no IPC).
"""
from __future__ import annotations

import contextlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import get_registry
from .api.room import router as room_router
from .api.routes import router as api_router
from .api.ws import router as ws_router
from .room.mcp_tools import mcp


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Any run left non-terminal belonged to a process that no longer
    # exists -- it is interrupted, not still running (D3).
    get_registry().reap_interrupted()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="AgentRoom", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/mcp", mcp.streamable_http_app(streamable_http_path="/"))

app.include_router(api_router)
app.include_router(room_router)
app.include_router(ws_router)
