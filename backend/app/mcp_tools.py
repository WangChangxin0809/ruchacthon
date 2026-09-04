"""The five AgentRoom MCP tools (arXiv 2608.23740), extended with a `scope`
field so a claim can be tagged worktree / person / team -- see ARCHITECTURE.md
for why those three tiers get different auto-merge vs. escalate behaviour.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer as FastMCP

from .room_state import room

mcp = FastMCP("agentroom")


@mcp.tool()
async def room_claim(path: str, actor_id: str, owner_id: str, worktree_id: str,
                      scope: str = "team") -> dict:
    """Atomically claim a file before editing it.

    scope: "worktree" (co-editing the same live buffer, CRDT-merged),
    "person" (same human's other agent/worktree), or "team" (a different
    teammate's agent -- collisions here are escalated to a human, not
    auto-resolved).
    """
    return await room.claim(path, actor_id, owner_id, worktree_id, scope)


@mcp.tool()
async def room_release(path: str, actor_id: str) -> dict:
    """Release a previously claimed file."""
    return await room.release(path, actor_id)


@mcp.tool()
async def room_broadcast(actor_id: str, owner_id: str, worktree_id: str,
                          scope: str, message: str, path: str = "") -> dict:
    """Append a message to the shared, append-only room log."""
    return await room.broadcast(actor_id, owner_id, worktree_id, scope, message, path)


@mcp.tool()
async def room_read(since_id: int = 0) -> list[dict]:
    """Read broadcast log entries after `since_id`."""
    return room.read(since_id)


@mcp.tool()
async def room_state() -> dict:
    """Current agents, active claims, and pending human escalations."""
    return room.state()


@mcp.tool()
async def room_edit_text(path: str, index: int, insert: str = "", delete: int = 0) -> str:
    """Apply a character-level edit to a worktree-scope shared CRDT buffer
    and return its current contents -- this is the actual real-time merge
    the AgentRoom paper measures, not a claim-only simulation."""
    return room.edit_text(path, index, insert, delete)


@mcp.tool()
async def submit_preview(actor_id: str, owner_id: str, title: str, summary: str,
                          html: str = "") -> dict:
    """Show a human what you made: a short summary and, optionally, a
    self-contained HTML snippet (a diff, a rendered page, a chart) that
    shows up on the dashboard's preview panel."""
    return await room.submit_preview(actor_id, owner_id, title, summary, html)
