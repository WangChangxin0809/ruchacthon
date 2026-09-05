from __future__ import annotations

from pydantic import BaseModel


class CreateProject(BaseModel):
    name: str
    root_path: str
    vcs: str = "git"


class CreateTask(BaseModel):
    title: str
    deps: list[str] = []


class SendMessage(BaseModel):
    message: str
    executor: str | None = None
    options: dict = {}


class Decision(BaseModel):
    decision: str
    reason: str = ""
    actor: str = "human"


class RoomDecision(Decision):
    escalation_id: str
