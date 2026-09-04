# AgentRoom backend

FastAPI dashboard API + WebSocket, with the AgentRoom MCP server (5 tools,
`room_claim/release/broadcast/read/state`, plus `room_edit_text` for the
worktree-scope CRDT demo) mounted in the same process at `/mcp`.

## Run

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt   # first time only
python3 -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8787
```

- Dashboard REST: `http://localhost:8787/api/...`
- Dashboard WebSocket: `ws://localhost:8787/ws`
- MCP endpoint (point a Claude Code `--mcp-config` at this): `http://localhost:8787/mcp`

If `pip` itself is missing and `apt install python3-pip` fails (no route to
Debian's mirrors, common in locked-down sandboxes): `pypi.org` is usually
still reachable, so `curl -sS -o get-pip.py https://bootstrap.pypa.io/get-pip.py
&& python3 get-pip.py --user --break-system-packages` bootstraps it directly.

## Why one process

The AgentRoom paper's premise is that concurrent agents see each other's
claims and edits immediately. Two separate processes (an MCP server per
agent, or per transport) would need to synchronize `RoomState` over IPC.
Mounting the MCP app inside the same FastAPI process means every `room_*`
tool call and every dashboard request read and write the exact same
in-memory object -- correct by construction, not by polling.

## Gate

`../scripts/gates/check_escalation_decisions.py` reads
`backend/data/room_log.jsonl` and `backend/data/decisions.jsonl`: any
`scope=team` claim conflict without a matching decision fails the gate.
That is the "no merge without a human" enforcement point.
