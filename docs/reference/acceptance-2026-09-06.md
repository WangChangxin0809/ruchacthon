# Acceptance, round 4 — agent definitions and your own API key

What was run by hand on 2026-09-06 against the code at that day's commits.
Unit tests prove mechanics; this file records what only a real process and a
real vendor can prove. The round-3 record is
[acceptance-2026-09-05.md](acceptance-2026-09-05.md).

## Batch 3 — agent definitions and agent-to-agent tools

Covered by `backend/tests/test_agents.py` (no model process): the four
built-ins seed idempotently, copy-to-create carries the source's fields and
prompt role, only an owner or team admin may edit, built-ins are never
editable, one default per role per team, a deleted definition resolves to the
built-in with `missing: true`, the run spec a definition produces (servers,
permission mode, turns, and a prompt that never names an absent tool),
addressing by session id / task id / `main` / name with its refusals,
`ask_human` posting a `needs_human` message and notifying every human member,
the room inbox cursor, and the schema 2 → 3 migration run twice.

Not yet proven with a real model: whether an orchestrator uses
`message_agent` and `ask_human` well in a long session. That needs paid runs.

## Batch 4 — presets, your own key, the conversion proxy

Server on port 8791, fresh data directory, NVIDIA NIM key supplied by the
project owner and read from a file the repository never sees. Scripts:
`b4-live.sh` and `b4-run.sh` in the session scratchpad.

| Step | Result |
|---|---|
| `GET /api/presets` | 200, 88 vendors in 5 categories |
| NVIDIA entry | `openai_chat`, needs routing, runnable |
| Create a preset profile with a key | 200, key recorded as set |
| `POST /api/profiles/{id}/check` | ok, 1363 ms, the model answered |
| `POST /api/profiles/{id}/models` | 200, 81 models from `.../v1/models` |
| Real Claude Code turn through the converter | run succeeded, the agent replied `PONG` |
| Route token after the run | 401, unknown route |
| `/proxy` with a spoofed `X-Forwarded-For` | 403 |
| Key in any API response | absent |
| Key in the log, the database, or an event | absent |
| Key on disk | only `secrets.json`, mode 0600 |

The vendor's base URL needed one fix to work: cc-switch stores the address
Claude Code is pointed at, and Claude Code appends `/v1/messages` itself, so
an OpenAI-chat upstream needs `/v1` added before `/chat/completions`. Without
it NVIDIA answered 404. Covered now by `test_providers.py`.

One observation worth keeping: the connection check asks for the single word
OK and this model replies with its reasoning first. The check treats any
2xx with a body as success, which is right — it is testing reachability, not
obedience.

## The frontend (batch 2)

Sixteen screens driven through a headless Chromium against a seeded database
(two users, a team, a DM, a group, a project with five tasks in four lanes, a
pending same-workspace conflict, an artifact, notifications). Each screen is
asserted on the text that must be on it and on the absence of any console or
page error; the script is `check.js` in the session scratchpad, not in the
repo, because it needs a browser this project does not depend on.

| Screen | Asserted |
|---|---|
| Login | the three shapes: first user, invited, needs an invite |
| Home | projects, and everything waiting on a human |
| Board | four lanes, branch, cost, who is on each task |
| Session (main) | the orchestrator's transcript, run banner |
| Session (worker) | transcript, changed files, Room conflict card, 停止 |
| Chat (group / DM) | messages, `@` highlighting, member stack |
| Inbox | mentions, questions, pending decisions, artifacts |
| Settings ×4 | general, team + invites, models, agent definitions |
| Preset picker | 88 vendors by category, search, custom |
| Agent editor | tools, permission mode, effort, turn and budget caps |
| New task | agent definition, model, isolation, dependency |
| Room drawer | claims, overlaps, the pending decision |
| Session members | who can see this session |

Three real bugs came out of it, each now covered by a test or a check:

1. `db.one` / `db.all` fetched rows outside the connection lock. One shared
   sqlite connection plus a page that fires six requests at once meant a
   cursor was left un-drained while another thread executed on it, and the
   row came back mangled (`dict(row)` raising `IndexError`). Rare with one
   user, routine with a real page. `concurrent_reads` in `test_workbench.py`
   fails without the fix.
2. The first registration was open to anyone. The box is reachable the moment
   it restarts, so the first stranger to find the port would have become its
   administrator — ADR 0005 says the deploy token gates that first account,
   and the code did not. `deploy_token_gate` fails without the fix.
3. A notification pointing at a conversation reloaded the page, and the
   conversation id was dropped from the URL before the chat mounted, so the
   link always landed on the wrong conversation.

## Migration rehearsal and deploy

The production database was still at schema 1. Rehearsed on a copy first:
schema 1 → 3 keeps every row (2 projects, 2 sessions, 1 run, 1 message),
gives every session a conversation with its agent as a member, seeds the four
built-in definitions and binds both `main` sessions to `orchestrator`, and is
a no-op when run twice. The first registration then claims the old projects
and sessions.

Deployed. On the server: `schema_version 3`, the same row counts, 3
conversations, 4 agent definitions, both sessions bound. `GET /api/auth`
reports `needs_deploy_token: true` and an anonymous registration is 403.

## Still unproven

- Two live workers sharing a workspace, Windows, Docker.
- A saved setup-token driving a run on the server: the server still has no
  Claude Code login, by the owner's choice, so nothing has run there.
- Every screen above was driven against seeded rows, not against a live run:
  the streaming path and the artifact-feedback round trip are covered by the
  backend tests, not by a browser.
