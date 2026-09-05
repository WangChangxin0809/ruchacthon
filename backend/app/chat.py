"""The main chat agent: one persistent conversation, distinct from the
subagents it can spawn. Kept separate from subagents.py because the main
agent's tool surface is a superset (it can spawn_subagent; subagents can't
spawn further subagents, on purpose -- unbounded delegation depth is a
runaway-cost bug waiting to happen, not a feature this demo needs).
"""
from __future__ import annotations

from .agent_loop import AgentIdentity, room_tools, run_turn
from .fs_tools import fs_tools
from .llm import LLMClient, ToolSpec
from .room_state import RoomState
from .subagents import SubagentManager

SYSTEM_PROMPT = (
    "You are the lead agent a human is talking to directly, working in a "
    "real checkout of this project (read_file/write_file/list_dir are real "
    "filesystem I/O, scoped to the project root). Before writing a file, "
    "call room_claim on its exact path first -- write_file refuses "
    "otherwise, this is enforced, not a suggestion. You can also delegate a "
    "self-contained task to a background subagent with spawn_subagent -- "
    "prefer delegating when a task can run independently, so the human "
    "isn't blocked waiting on you. Call submit_preview when you finish "
    "something worth showing, not for routine updates."
)


class MainChat:
    def __init__(self, room: RoomState, llm: LLMClient, subagents: SubagentManager,
                 owner_id: str = "human", worktree_id: str = "wt-main"):
        self.room = room
        self.llm = llm
        self.subagents = subagents
        self.identity = AgentIdentity(actor_id="agent-main", owner_id=owner_id,
                                       worktree_id=worktree_id, scope="team")
        self.history: list[dict] = []

    def _tools(self) -> tuple[list[ToolSpec], dict]:
        specs, fns = room_tools(self.room, self.identity)
        fs_specs, fs_fns = fs_tools(self.room, self.identity)
        specs, fns = specs + fs_specs, {**fns, **fs_fns}

        async def spawn_subagent(args: dict) -> dict:
            actor_id = self.subagents.spawn(
                owner_id=self.identity.owner_id,
                worktree_id=args.get("worktree_id") or "wt-sub",
                task=args["task"],
            )
            return {"ok": True, "actor_id": actor_id}

        specs = specs + [ToolSpec(
            "spawn_subagent",
            "Delegate a self-contained task to a new background subagent. "
            "Returns immediately with its actor_id; poll room_state or the "
            "dashboard to see its progress.",
            {"type": "object", "properties": {
                "task": {"type": "string"},
                "worktree_id": {"type": "string"},
            }, "required": ["task"]},
        )]
        fns = {**fns, "spawn_subagent": spawn_subagent}
        return specs, fns

    async def send(self, message: str) -> list[dict]:
        self.history.append({"role": "user", "content": message})
        specs, fns = self._tools()
        log = await run_turn(self.llm, history=self.history, system=SYSTEM_PROMPT,
                              tools=specs, tool_fns=fns)
        self.history = log.messages
        return log.events
