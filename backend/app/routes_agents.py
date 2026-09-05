"""Agent definition routes. Built by `make_router(svc)` like the other route
modules, so nothing here imports main.py.

A definition is visible to its owner, to its team, and -- for the four
built-ins -- to everyone; editing needs the owner or a team admin, and the
built-ins are never editable. `defaults` in the list response is what the UI
preselects: the team's default per role, falling back to the built-in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .agents import PERMISSION_LABELS, PERMISSION_MODES, ROLES
from .auth import me


class AgentIn(BaseModel):
    copy_from: str | None = None
    team_id: str | None = None
    name: str
    description: str | None = None
    role: str | None = None
    system_prompt: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None
    permission_mode: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    can_spawn: bool | None = None
    effort: str | None = None


class AgentPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    role: str | None = None
    system_prompt: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None
    permission_mode: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    can_spawn: bool | None = None
    effort: str | None = None


class DefaultIn(BaseModel):
    team_id: str


def make_router(svc) -> APIRouter:
    r = APIRouter()
    agents, teams = svc.agents, svc.teams

    def _team(user: dict, team_id: str | None) -> str | None:
        """A team the caller belongs to, or None for a personal definition."""
        if not team_id:
            return None
        teams.require_team(user, team_id)
        return team_id

    @r.get("/api/agents")
    def list_agents(team_id: str | None = None, role: str | None = None, user: dict = Depends(me)):
        if role and role not in ROLES:
            raise HTTPException(400, "role 必须是 orchestrator | worker | any")
        if team_id:
            teams.require_team(user, team_id)
        items = [agents.public(d, user) for d in agents.visible(user, team_id, role)]
        return {"items": items, "defaults": agents.defaults(team_id), "permission_labels": PERMISSION_LABELS,
                "roles": list(ROLES), "permission_modes": list(PERMISSION_MODES)}

    @r.post("/api/agents", status_code=201)
    def create_agent(body: AgentIn, user: dict = Depends(me)):
        d = agents.create(user, body.model_dump(exclude_unset=True), _team(user, body.team_id))
        return agents.public(d, user)

    @r.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str, user: dict = Depends(me)):
        return agents.public(agents.require_visible(user, agent_id), user)

    @r.patch("/api/agents/{agent_id}")
    def update_agent(agent_id: str, body: AgentPatch, user: dict = Depends(me)):
        d = agents.require_editable(user, agent_id)
        return agents.public(agents.update(d, body.model_dump(exclude_unset=True)), user)

    @r.delete("/api/agents/{agent_id}", status_code=204)
    def delete_agent(agent_id: str, user: dict = Depends(me)):
        agents.delete(agents.require_editable(user, agent_id))

    @r.post("/api/agents/{agent_id}/default")
    def set_default(agent_id: str, body: DefaultIn, user: dict = Depends(me)):
        teams.require_team(user, body.team_id, min_role="admin")
        d = agents.require_visible(user, agent_id)
        return {"defaults": agents.set_default(d, body.team_id)}

    return r
