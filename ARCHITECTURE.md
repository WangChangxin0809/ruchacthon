# Architecture

- **Covers**: how this system works, for someone who does not yet know what to
  ask. Read on demand, so it may be long.
- **Does not cover**: how to perform a task (`docs/how-to/`), why a specific
  choice was made (`docs/decisions/`).

## What it does

CC Workbench is a local, multi-agent development workbench whose only
execution engine is Claude Code. A human talks to a **main agent** in a
session; the main agent (or the human directly) spawns **workers**, each a
separate `claude` process in its own git worktree. Workers hand back
**artifacts** (Markdown, images, HTML, files, diffs, managed dev servers); the
human reviews them and feedback flows back into the worker's own session.
Workers see each other through the **Room** (claims with leases, overlap
notices, broadcasts, handoffs), and a same-workspace claim conflict is never
auto-resolved: it becomes a pending human decision that the blocked worker
waits on. Everything is persisted in one SQLite file with an ordered event
log, so a browser reconnects with `since=<seq>` and a server restart tells the
truth about which runs died.

Ideas borrowed, and since docs/decisions/0004 code copied with attribution: Agent Orchestrator
(orchestrator-plus-workers, board from run facts), DeepSeek Harness (task
board with write-scope overlap warnings), AgentRoom arXiv 2608.23740 (claims,
escalation to a human at the team boundary), CC Switch (provider profiles as
per-run config, never rewriting the user's global CC config).

## Domain model

| Entity | Meaning | Key fields |
|---|---|---|
| Project | a directory the user works in | `root_path`, `is_git` |
| Workspace | where a Run may write: `main` (the project dir), `worktree` (own branch), `dir` (copy; no merge) | `path`, `branch`, `base_ref`, `edit_mode` |
| Task | a unit of work with its own review and merge state | `review_status`, `merge_status`, `depends_on` |
| Session | one continuous conversation (main, or one per worker task) | `cc_session_id` |
| Run | one execution attempt inside a session | `status`, `outcome`, `attempt_no`, `profile_snapshot`, `pid` |
| Artifact | something a run hands to a human, versioned per task | `kind`, `version`, `status` |
| Room: Claim / RoomMessage / Decision | who owns which path; overlap/broadcast/handoff; the human's ruling | `expires_at`, `blocked_run_id` |
| ProviderProfile | which endpoint a run talks to | `kind`, `models`, `credential_ref` (a name in the write-only secret store, never a value) |
| Channel / ChatMessage | people talking to people; no model reads it | `author`; a message can be handed to the work area as a Task |

Task status on the board is **derived**, never stored: latest run status
(`queued/running/needs_input/failed/cancelled/interrupted/exhausted`), then
`review_status`, then `merge_status`. A process exiting cleanly puts the task
in `in_review`; only a human moves it past that.

## Codemap

| Path | Holds | Talks to |
|---|---|---|
| `backend/app/cc_runner.py` | the only model-calling code: one `claude` subprocess per Run via the Agent SDK; stream, interrupt, mid-turn sends | `runs.py` |
| `backend/app/runs.py` | RunManager: scheduling, concurrency, dependencies, cancel, deliver, restart reconciliation, and the run-bound tool servers (`workbench` for main, `room` for workers) | everything below |
| `backend/app/room.py` | claims, leases, overlap vs conflict, handoff, decisions | `runs.deliver` |
| `backend/app/artifacts.py`, `devservers.py` | artifact versions + feedback; managed dev-server processes | `workspaces` |
| `backend/app/workspaces.py` | worktree / dir workspaces, diff, merge | git |
| `backend/app/providers.py`, `secrets_store.py`, `ccconfig.py` | Provider Profiles + compat check; write-only 0600 secret store; CC install/config/login discovery | `cc_runner` (env) |
| `backend/app/shared_edit.py` | EXPERIMENTAL CRDT merge for `Write` in shared-edit workspaces | `runs.py` hooks |
| `backend/app/db.py`, `events.py`, `main.py` | SQLite schema; seq'd event bus; FastAPI + WebSocket | frontend |
| `frontend/src/` | React workbench laid out like Agent Orchestrator: sidebar (projects → sessions), home (start actions, 需要你, recent projects), per-project kanban board, session view (timeline + composer + inspector: summary/preview/files), Room drawer, people-only chat, dsh-style settings | `/api`, `/ws` |
| `scripts/gates/check_escalation_decisions.py` | the merge gate: no pending decisions | `workbench.db` |

## Invariants

1. A same-workspace claim conflict is never auto-resolved — `Room._conflict`
   creates a pending Decision and the claim is refused until a human decides;
   `check_escalation_decisions.py` blocks `ci.sh` while one is pending.
2. Every model call goes through `cc_runner.CCRun` bound to a Run row with a
   real `cwd`, session, task and profile snapshot — there is no other client.
3. A run's status changes only through `RunManager._set_status`, which writes
   the row and emits `run_status` together; after a restart every run whose
   process is gone is `interrupted`, never left `running`.
4. Tool identity is a closure over the Run (`_room_tools(run)`), so a model
   cannot claim, hand off or submit as anybody else.
5. Secrets never leave the server process: a key entered in the UI goes into
   the write-only store (`secrets_store.py`, `0600`) and profiles keep only its
   *name*; the API answers "set / not set", snapshots list keys only, error
   strings pass through `providers.scrub`.
6. The event log's `seq` is monotonic and every persisted event is replayable
   from any point; stream deltas are the only unpersisted events.

## What is not enforced by code

- **Docker** (`docker-compose.yml`) is kept from the previous architecture and
  is unverified: the engine is the host's Claude Code login, so the container
  would need `claude` installed and the user's `~/.claude` mounted.
- **Shared-edit mode** protects `Write` through hooks only; a shell command
  writing the same file in the merge window wins. It is labelled experimental
  in the UI for that reason (see `shared_edit.py`'s docstring).
- **Windows**: the design avoids tmux/pty (SDK spawns `claude` directly;
  `psutil` for process checks; `git worktree` for isolation) but has not yet
  been run on a Windows machine.
