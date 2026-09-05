"""Background subagents: each one is an independent agent_loop run as an
asyncio task, with its own identity (so its room_claim calls carry a real
owner_id/actor_id), reporting status through the same RoomState.agents
list the dashboard already renders.

This -- not scripts/demo/run_two_agents.py -- is meant to become the real
two-(or-more)-agent demo once a model key exists: spawn two subagents with
different owner_id against the same file and watch a genuine LLM decide to
call room_claim, not a script pretending one did.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .agent_loop import AgentIdentity, room_tools, run_turn
from .fs_tools import fs_tools
from .llm import LLMClient
from .room_state import RoomState

SYSTEM_PROMPT = (
    "You are a coding agent working in a real checkout of this repository "
    "alongside other agents (read_file/write_file/list_dir are real "
    "filesystem I/O, scoped to the project root). Before editing a file, "
    "call room_claim with the right scope: 'worktree' if you share a live "
    "buffer with another agent, 'person' if another of your own agents "
    "might touch it, 'team' if a teammate's agent might. write_file refuses "
    "until you hold the claim -- this is enforced, not a suggestion. If "
    "room_claim reports a conflict, do not just barrel through: call "
    "room_broadcast to say what you're doing and why it's safe (or stop if "
    "it isn't), and expect a human to decide team-scope conflicts, not you. "
    "Call room_release when you're done with a file."
)


@dataclass
class Subagent:
    identity: AgentIdentity
    task: str
    status: str = "queued"  # queued -> running -> done | failed
    events: list[dict] = field(default_factory=list)
    error: str | None = None


class SubagentManager:
    def __init__(self, room: RoomState, llm: LLMClient):
        self.room = room
        self.llm = llm
        self.subagents: dict[str, Subagent] = {}

    def spawn(self, owner_id: str, worktree_id: str, task: str, scope: str = "team") -> str:
        actor_id = f"agent-{uuid.uuid4().hex[:8]}"
        identity = AgentIdentity(actor_id=actor_id, owner_id=owner_id,
                                  worktree_id=worktree_id, scope=scope)
        sub = Subagent(identity=identity, task=task)
        self.subagents[actor_id] = sub
        import asyncio
        asyncio.create_task(self._run(sub))
        return actor_id

    async def _run(self, sub: Subagent) -> None:
        sub.status = "running"
        await self.room.set_agent_status(sub.identity.actor_id, sub.identity.owner_id,
                                          sub.identity.worktree_id, "working", sub.task)
        tools, fns = room_tools(self.room, sub.identity)
        fs_specs, fs_fns = fs_tools(self.room, sub.identity)
        tools, fns = tools + fs_specs, {**fns, **fs_fns}
        try:
            log = await run_turn(
                self.llm,
                history=[{"role": "user", "content": sub.task}],
                system=SYSTEM_PROMPT,
                tools=tools,
                tool_fns=fns,
            )
            sub.events = log.events
            sub.status = "done"
            # "in_review", not "ready_to_merge": a subagent finishing its own
            # task is not the same as a human having looked at it -- that
            # judgment is exactly what this track asks not to skip.
            await self.room.set_agent_status(sub.identity.actor_id, sub.identity.owner_id,
                                              sub.identity.worktree_id, "in_review", sub.task)
        except Exception as exc:  # a subagent's own failure must not crash the server
            sub.error = f"{type(exc).__name__}: {exc}"
            sub.status = "failed"
            await self.room.set_agent_status(sub.identity.actor_id, sub.identity.owner_id,
                                              sub.identity.worktree_id, "needs_input",
                                              f"failed: {sub.error}")

    def list(self) -> list[dict]:
        return [
            {
                "actor_id": s.identity.actor_id,
                "owner_id": s.identity.owner_id,
                "worktree_id": s.identity.worktree_id,
                "task": s.task,
                "status": s.status,
                "events": s.events,
                "error": s.error,
            }
            for s in self.subagents.values()
        ]
