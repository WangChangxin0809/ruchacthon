# Acceptance evidence — 2026-09-05

- **Covers**: what was actually exercised against a real Claude Code
  (`claude 2.1.259`, Agent SDK 0.2.152, model `claude-sonnet-5` for workers),
  with the ids to find it in the event log, and what remains unverified.
- **Does not cover**: the mechanics proven without a model — that is
  `backend/tests/test_workbench.py`.

All runs below were made on a scratch git project registered as
`prj_8dc972a7d9f9`; the event log and SQLite file lived in a scratch
`WORKBENCH_DATA_DIR`. Costs are as reported by Claude Code.

| # | Requirement | What happened | Evidence |
|---|---|---|---|
| 1 | Real CC completes a file change and produces an artifact | Worker "Add multiply to calc" (`run_57ce0f90e5cc`, worktree `wb/add-multiply-to-calc-8c8e25`) added `multiply()`, wrote `test_calc.py`, ran it, submitted a `diff` and a `markdown` artifact; 15 turns, $0.14 | `run_status succeeded`, two `artifact` events |
| 2 | Two workers in separate workspaces, viewable and cancellable separately | "Add multiply" and "Slow doc task" ran concurrently in two worktrees; each has its own session/transcript | tasks `task_b7cddb4e10db`, `task_dbf813d7ee78` |
| 3 | Cancel ≠ complete | "Long compute" (`run_70407bb83f42`) interrupted mid `python3 -c time.sleep(120)`: run `cancelled` / outcome `error_during_execution`, `result.txt` never written, board column "需要处理" | `run_cancelling` → `run_status cancelled` |
| 4 | Restart keeps history and tells the truth | Server restarted with Room A/B live (pids 392303/392432): both runs → `interrupted` with reason, processes gone, claims released; all earlier runs/messages/artifacts intact | `server_started{recovered_runs}` |
| 5 | Reconnect loses/duplicates nothing | A WS client reconnecting with `since=<seq>` across 2 server restarts saw 215 persisted events: 215 unique seqs, 0 gaps, 0 duplicates; 78 unpersisted `stream_delta`s | scratch `ws-events.jsonl` |
| 6 | Preview shows the worker's real product | Diff artifact content == `git diff` of that worker's worktree; markdown artifact from the same run | `art_f84428caecfe` (md v1), diff v1 |
| 7 | Feedback reaches the right worker | `request_changes` on the markdown artifact → new run `run_7954d025b1e6` (attempt 2) resumed the same CC session, added `subtract()`, resubmitted; markdown became v2, v1 `superseded`, attempt 1's evidence untouched | `artifact_feedback{delivery:new_run}` |
| 7b | Message into a *live* run | "HELLO-FROM-NIC-7731" sent while worker 6 was inside a 60 s Bash; the worker quoted it verbatim and the run ended `succeeded` | `run_bd3a087b96ef` |
| 8 | Room conflict + decision have real effect | Room A claimed `calc.py`; Room B (same workspace) claimed it → pending `dec_3e45e0875531`, B's run `needs_input`. Human approved B: A received "reassigned… stop editing" mid-Bash and stopped; B received "APPROVED" and proceeded; A's claim released, B granted | `decision`, `run_blocked`, `run_unblocked`, both sessions' `room`-authored user turns |
| 9 | Profiles do not overwrite each other | Profiles are injected as per-run `env`; each run stores a secret-free `profile_snapshot`. `claude_code_default` profile compat check: stream ✓ tool ✓ `compat-ok`. Gateway profile with missing credential: refused before spawning, error names the variable only | `/api/profiles/{id}/check` |
| 10 | No secrets in responses or logs | `extra_env` with a `*_KEY` name refused at create; grep of server log, WS stream and DB for token values: none; unit test asserts snapshot/scrub | `test_workbench.py` providers section |
| 11 | Review ≠ merge | Approving the review moved the task to `ready_to_merge`; merge was a separate call that produced a `--no-ff` merge in the project repo and `done` on the board | `git log` of the scratch project |

## Round 2 (same day): dashboard, projects, login state, chat, dsh-style config

| # | Requirement | What happened | Evidence |
|---|---|---|---|
| 12 | Model chosen in the composer reaches the run | Main-session message sent with `model=claude-haiku-4-5-20251001`; the run's `profile_snapshot.model` is that id and the reply was the requested `PONG` | `run_a76bd47aaf82` |
| 13 | Projects can be created from the browser | `POST /api/projects {name}` → `git init` under `WORKBENCH_PROJECTS_DIR`; `{git_url}` → clone of `octocat/Hello-World` registered as a project | `prj_4ed843f1528d`, `prj_9b7674851fa4` |
| 14 | Chat is people-only and live | Channel `前端组` created, message posted, appears via the `chat_message` event; nothing reaches a model unless 「派给 agent」 is used (that path is `POST /api/projects/{id}/tasks`, item 2) | `ch_92316aa420de` |
| 15 | Secrets are write-only | Profile key saved through `PATCH /api/profiles/{id}{secret}`; `GET /api/profiles` returns only `credential_set`/`credential_source`; the store file is `0600` | `test_workbench.py` secrets section |
| 16 | Login state is shown, not assumed | `/api/cc/status` runs `claude auth status --json` for the server login and, separately, for a token saved on the settings page; the header dot reflects `effective` | header, 设置 → Claude Code |

## Round 3 (same day): the UI copied from Agent Orchestrator

| # | Requirement | What happened | Evidence |
|---|---|---|---|
| 17 | AO's layout on our backend | Sidebar (projects → sessions), home (start actions, 需要你, recent projects), four-lane board, session view (timeline / composer / inspector), settings modal, people-only chat; every page rendered against the live local backend with real runs | screenshots `wb-home/board/session-main/session-worker/chat/settings.png` in the session scratchpad |
| 18 | Model chosen in the new-task dialog reaches the worker run | `POST /api/projects/{id}/tasks` with `model=claude-haiku-4-5-20251001`; the worker run's `profile_snapshot.model` is that id and the reply was the requested `PONG` | `task_e2226c7aae6b`, `run_848003c722eb` |

## Not verified (honest list)

- **Real provider paths other than the CC login**: Bedrock, Vertex, Foundry
  and an `ANTHROPIC_BASE_URL` gateway were exercised only up to "credential
  missing → refused" and the env-mapping unit test. No live gateway was
  available.
- **Failure feedback from a *reachable but wrong* endpoint** (e.g. an
  OpenAI-style server behind `ANTHROPIC_BASE_URL`): the scrubbed error path
  is unit-tested, not observed live.
- **Project-level CC config inheritance** (`CLAUDE.md`, `.mcp.json`, skills,
  hooks) is delegated to Claude Code via `setting_sources`; only *user*
  settings were asserted (auth came from there). See `/api/cc/discovery`
  `support` table.
- **Shared-edit mode with two live CC workers**: the CRDT merge is
  unit-tested with simulated replicas; a two-process run was not made.
  Shell writes are unprotected by design (see `shared_edit.py`).
- **Dev-server artifacts** (`start_devserver`): manager code exercised only by
  hand-started subprocess semantics, not by a model calling it.
- **Windows**: not run on Windows yet.
- **Docker**: compose files updated but not built.
- **Task dependencies** (`depends_on`) wait loop: unit-level only.
- **A saved `CLAUDE_CODE_OAUTH_TOKEN` driving a run on the cloud server**: the
  server has no Claude Code login yet by the owner's choice; the token path is
  verified only by `claude auth status` against a saved token, not by a run.
- **「派给 agent」 from chat with a live model**: the dialog calls the same
  task-creation endpoint as the sidebar (item 2); the round trip from a chat
  message to a worker transcript was not run against a model.
