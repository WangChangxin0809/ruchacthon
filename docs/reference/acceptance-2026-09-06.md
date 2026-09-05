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

## Still unproven

- The frontend for any of this (batch 2 is not written).
- Two live workers sharing a workspace, Windows, Docker.
- A saved setup-token driving a run on the server: the server still has no
  Claude Code login, by the owner's choice.
