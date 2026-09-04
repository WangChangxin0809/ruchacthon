# Architecture

- **Covers**: how this system works, for someone who does not yet know what to
  ask. Read on demand, so it may be long.
- **Does not cover**: how to perform a task (`docs/how-to/`), why a specific
  choice was made (`docs/decisions/`).

## What it does

AgentRoom gives concurrent Claude Code agents a shared coordination layer,
exposed as five MCP tools (`room_claim`, `room_release`, `room_broadcast`,
`room_read`, `room_state`; see arXiv 2608.23740). A claim is tagged with a
`scope`: `worktree` (agents literally co-editing the same buffer -- resolved
by a real pycrdt CRDT merge, never escalated), `person` (one human's several
agents -- tracked, rarely escalated), or `team` (different teammates' agents
-- escalated to a human by default, because that is a staffing decision, not
a text-merge problem).

A `team`-scope conflict is written to `backend/data/room_log.jsonl` and
surfaced on the dashboard. `scripts/gates/check_escalation_decisions.py`
reads that log plus `backend/data/decisions.jsonl` and fails the build until
every conflict has a matching human decision -- the enforcement point for
"remove the human and the workflow stops working."

## Codemap

| Directory | Holds | Talks to |
|---|---|---|
| `backend/app/` | FastAPI dashboard API + WebSocket, MCP tools, `RoomState` (claims, CRDT docs, escalations) | frontend (REST/WS), any MCP client (`/mcp/`) |
| `frontend/` | React dashboard: agent status board, escalation queue | `backend/app` |
| `scripts/gates/check_escalation_decisions.py` | the merge gate | `backend/data/*.jsonl` |
| `scripts/demo/` | scripted two-agent conflict for a live demo | the running backend, via a real MCP client |
| `demo/target-app/` | toy file the demo agents race to edit | — |

## Invariants

1. A `team`-scope claim conflict is never silently auto-resolved — enforced in `backend/app/room_state.py::RoomState.claim`.
2. No merge proceeds while a logged conflict lacks an approved decision — enforced by `scripts/gates/check_escalation_decisions.py`, wired into `ci.sh`.
3. The MCP tool surface and the dashboard read/write the same in-process `RoomState` — enforced by mounting the MCP app inside the same FastAPI process (`backend/app/main.py`), not a second process reading the same files.

## Constraints that are not visible in the code

- This was built inside a sandboxed cheese topic whose Bash tool has no usable
  Anthropic credentials by design, so `scripts/demo/run_two_agents.py` drives
  the real MCP server with a scripted client rather than two live `claude -p`
  processes. The `.mcp.json` at the repo root is real and auto-discovered by
  any normally-authenticated Claude Code session — the constraint is this
  sandbox, not the design.
- `apt` cannot reach Debian's mirrors from this sandbox; `pip` and `node` were
  bootstrapped from `pypi.org` / `nodejs.org` directly (see `backend/README.md`).
