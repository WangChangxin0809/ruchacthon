# Plan: CC workbench

Goal: replace the hand-written LLM loop with a Claude-Code-only local
multi-agent workbench (main agent, background workers in worktrees, real
board, artifact preview + feedback loop, Room coordination, Provider
Profiles). Decision: [0003](../../decisions/0003-claude-code-only-harness.md).

Abort condition: the SDK cannot drive `claude` on the target machine at all
(auth or spawn) — then the product has no engine and nothing below matters.

| Step | Milestone | Proof |
|---|---|---|
| A | CC channel verified; domain model in SQLite; main-agent streaming chat persisted with seq events | `backend/tests/test_workbench.py`, a real run with a `Write` tool event in `events` |
| B | Workers in git worktrees; cancel; restart marks dead runs `interrupted`; board from run facts | two concurrent worker runs, cancel one, restart backend, statuses correct |
| C | Artifacts (md/image/html/file/diff/devserver) with versions; feedback delivered to the worker's session | feedback appears in the worker's transcript as a user turn |
| D | Room: run-bound identity, leases, heartbeat, overlap, handoff, human decision reaching the run | two workers claim one path → decision → loser receives the decision message |
| E | CC config discovery; Provider Profiles; compatibility check without leaking secrets | `/api/cc/discovery`, `/api/profiles/{id}/check` |
| F | Shared CRDT edit mode — experimental, hook-routed writes only | `backend/tests/test_shared_edit.py`; Bash writes documented as unprotected |

Old callers removed at the end of B (`agent_loop.py`, `llm.py`, `chat.py`,
`subagents.py`, `fs_tools.py`, `mcp_tools.py`, `scripts/demo/`).
