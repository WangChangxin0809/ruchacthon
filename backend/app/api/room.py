"""Room endpoints -- unchanged surface from the pre-SQLite dashboard, just
reading through the migrated `room.state.RoomState` (backend/app/room/state.py)
instead of the old jsonl-backed one. Not part of this round's redesign
(D4 identity binding is a later round).
"""
from __future__ import annotations

from fastapi import APIRouter

from ..room.state import room
from .schemas import Decision, RoomDecision

router = APIRouter(prefix="/api")


@router.get("/room/state")
def room_state():
    return room.state()


@router.get("/agents")
def list_agents():
    return room.state()["agents"]


@router.get("/escalations")
def list_escalations():
    return room.list_escalations(pending_only=True)


@router.get("/log")
def list_log(since_id: int = 0):
    return room.read(since_id)


@router.post("/room/decisions")
async def room_decide(body: RoomDecision):
    return await room.decide(body.escalation_id, body.decision, body.reason, body.actor)


@router.post("/escalations/{escalation_id}/decide")
async def decide(escalation_id: str, body: Decision):
    return await room.decide(escalation_id, body.decision, body.reason, body.actor)


@router.get("/previews")
def list_previews():
    return room.list_previews()
