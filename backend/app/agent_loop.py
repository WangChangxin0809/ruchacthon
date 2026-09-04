"""One agent's turn loop: model call -> tool calls -> model call, until the
model stops asking for tools. This is the harness for both the main chat
agent and every subagent -- see subagents.py, which just runs N of these
concurrently with different owner/actor identities.

Tools are called directly against RoomState, not through the MCP protocol:
MCP is for *external* processes (a real Claude Code session, per the root
.mcp.json) to reach this same server. An agent running inside this process
already shares the Python object -- going out to HTTP and back would be
pure overhead with no isolation benefit, since both call sites end up at
the same `RoomState` instance either way.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .llm import ChatResult, LLMClient, ToolCall, ToolSpec
from .room_state import RoomState

MAX_STEPS = 8  # a runaway tool-call loop is a bug, not a feature


@dataclass
class AgentIdentity:
    actor_id: str
    owner_id: str
    worktree_id: str
    scope: str = "team"


ToolFn = Callable[[dict], Awaitable[dict]]


def room_tools(room: RoomState, identity: AgentIdentity) -> tuple[list[ToolSpec], dict[str, ToolFn]]:
    """The AgentRoom tool surface, bound to one identity, as plain callables.

    Same five verbs the MCP server exposes (see mcp_tools.py) -- kept as two
    independent bindings on purpose, so an in-process agent and an external
    MCP client exercise the identical RoomState methods without one
    depending on the other's transport.
    """

    async def claim(args: dict) -> dict:
        return await room.claim(args["path"], identity.actor_id, identity.owner_id,
                                 identity.worktree_id, args.get("scope", identity.scope))

    async def release(args: dict) -> dict:
        return await room.release(args["path"], identity.actor_id)

    async def broadcast(args: dict) -> dict:
        return await room.broadcast(identity.actor_id, identity.owner_id, identity.worktree_id,
                                     args.get("scope", identity.scope), args["message"],
                                     args.get("path", ""))

    async def state(_args: dict) -> dict:
        return room.state()

    async def preview(args: dict) -> dict:
        return await room.submit_preview(identity.actor_id, identity.owner_id,
                                          args["title"], args["summary"], args.get("html", ""))

    specs = [
        ToolSpec("room_claim", "Claim a file before editing it (scope: worktree|person|team).",
                 {"type": "object", "properties": {
                     "path": {"type": "string"},
                     "scope": {"type": "string", "enum": ["worktree", "person", "team"]},
                 }, "required": ["path"]}),
        ToolSpec("room_release", "Release a previously claimed file.",
                 {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
        ToolSpec("room_broadcast", "Post a message to the shared room log.",
                 {"type": "object", "properties": {
                     "message": {"type": "string"}, "path": {"type": "string"},
                 }, "required": ["message"]}),
        ToolSpec("room_state", "Read current agents, claims, and pending escalations.",
                 {"type": "object", "properties": {}}),
        ToolSpec("submit_preview",
                 "Show a human what you made: a short summary and, optionally, "
                 "a self-contained HTML snippet to render (a diff, a rendered "
                 "page, a chart). Call this when you finish something worth "
                 "reviewing, not for routine progress updates.",
                 {"type": "object", "properties": {
                     "title": {"type": "string"},
                     "summary": {"type": "string"},
                     "html": {"type": "string"},
                 }, "required": ["title", "summary"]}),
    ]
    fns = {"room_claim": claim, "room_release": release, "room_broadcast": broadcast,
           "room_state": state, "submit_preview": preview}
    return specs, fns


@dataclass
class TurnLog:
    """What actually happened in one run() call -- for the chat/preview UI,
    not the model. Kept separate from the LLM message history because the
    UI wants tool calls and results as structured events, not prose."""
    messages: list[dict] = field(default_factory=list)  # OpenAI/Anthropic-shape history
    events: list[dict] = field(default_factory=list)     # {type, ...} for the UI/session log


async def run_turn(llm: LLMClient, history: list[dict], system: str,
                    tools: list[ToolSpec], tool_fns: dict[str, ToolFn]) -> TurnLog:
    log = TurnLog(messages=list(history))
    for _ in range(MAX_STEPS):
        result: ChatResult = await llm.chat(log.messages, tools=tools, system=system)
        log.messages.append(_assistant_message(llm.provider, result))
        log.events.append({"type": "assistant_text", "text": result.text})

        if not result.tool_calls:
            return log

        outputs = []
        for call in result.tool_calls:
            fn = tool_fns.get(call.name)
            if fn is None:
                output = {"error": f"unknown tool {call.name!r}"}
            else:
                output = await fn(call.arguments)
            log.events.append({"type": "tool_call", "name": call.name,
                                "arguments": call.arguments, "result": output})
            outputs.append((call, output))

        if llm.provider == "anthropic":
            # One call each turn expects one user message with every
            # tool_result block inside it, not one message per result.
            log.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(o)}
                for c, o in outputs
            ]})
        else:
            log.messages.extend(_tool_result_message(llm.provider, c, o) for c, o in outputs)
    log.events.append({"type": "step_limit_reached", "limit": MAX_STEPS})
    return log


def _assistant_message(provider: str, result: ChatResult) -> dict:
    if provider == "anthropic":
        blocks = []
        if result.text:
            blocks.append({"type": "text", "text": result.text})
        for c in result.tool_calls:
            blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
        return {"role": "assistant", "content": blocks}
    # openai wire format
    msg: dict = {"role": "assistant", "content": result.text or None}
    if result.tool_calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in result.tool_calls
        ]
    return msg


def _tool_result_message(provider: str, call: ToolCall, output: dict) -> dict:
    # openai wire format only -- the anthropic branch batches every result
    # from a turn into a single user message above, since that's what the
    # API expects when a turn made more than one tool call at once.
    assert provider != "anthropic"
    return {"role": "tool", "tool_call_id": call.id, "content": json.dumps(output)}
