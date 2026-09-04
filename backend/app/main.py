"""Dashboard API + WebSocket, with the AgentRoom MCP server mounted in the
same process so both sides share one in-memory RoomState -- no IPC, no
polling between the two.
"""
from __future__ import annotations

import asyncio
import contextlib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .chat import MainChat
from .llm import LLMClient, NotConfiguredError
from .mcp_tools import mcp
from .room_state import room
from .subagents import SubagentManager

llm = LLMClient()
subagents = SubagentManager(room, llm)
main_chat = MainChat(room, llm, subagents)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="AgentRoom Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/mcp", mcp.streamable_http_app(streamable_http_path="/"))


@app.get("/api/agents")
def list_agents():
    return room.state()["agents"]


@app.get("/api/escalations")
def list_escalations():
    return room.list_escalations(pending_only=True)


@app.get("/api/log")
def list_log(since_id: int = 0):
    return room.read(since_id)


class Decision(BaseModel):
    decision: str  # "approve" | "reject"
    reason: str = ""
    actor: str = "human"


@app.post("/api/escalations/{escalation_id}/decide")
async def decide(escalation_id: str, body: Decision):
    return await room.decide(escalation_id, body.decision, body.reason, body.actor)


@app.get("/api/health")
def health():
    return {"ok": True, "llm_configured": llm.is_configured()}


class ChatMessage(BaseModel):
    message: str


@app.post("/api/chat")
async def chat(body: ChatMessage):
    try:
        events = await main_chat.send(body.message)
    except NotConfiguredError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "events": events}


@app.get("/api/chat/history")
def chat_history():
    return main_chat.history


class SpawnSubagent(BaseModel):
    owner_id: str = "human"
    worktree_id: str = "wt-sub"
    task: str


@app.post("/api/subagents")
async def spawn_subagent(body: SpawnSubagent):
    actor_id = subagents.spawn(body.owner_id, body.worktree_id, body.task)
    return {"ok": True, "actor_id": actor_id}


@app.get("/api/subagents")
def list_subagents():
    return subagents.list()


@app.get("/api/previews")
def list_previews():
    return room.list_previews()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue = room.subscribe()
    try:
        # Prime the client with a full snapshot before streaming live events.
        await websocket.send_json({"type": "snapshot", "data": room.state()})
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        room.unsubscribe(queue)
