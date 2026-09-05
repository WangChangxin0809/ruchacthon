# CC Workbench backend

FastAPI + one SQLite file. Every model call is a `claude` subprocess driven
through the official Agent SDK (`app/cc_runner.py`); there is no other model
client. See [../ARCHITECTURE.md](../ARCHITECTURE.md) for the codemap.

## Run

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt   # first time only
python3 -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8787
```

- REST: `http://localhost:8787/api/...` (`/docs` for the OpenAPI page)
- Events: `ws://localhost:8787/ws?since=<seq>&project_id=<id>` — replays
  every persisted event after `since`, then streams live.

Environment: `WORKBENCH_DATA_DIR` (default `backend/data`),
`WORKBENCH_MODEL` (model for runs without a profile), `WORKBENCH_MAX_CONCURRENT_RUNS`
(default 3). Provider Profile credentials are read from the server's
environment by the *name* stored in the profile.

## Test

```bash
python3 backend/tests/test_workbench.py
```

No Claude Code needed: events, Room, artifacts, profiles, restart
reconciliation, CRDT merge. Real-model behaviour is recorded in
[../docs/reference/acceptance-2026-09-05.md](../docs/reference/acceptance-2026-09-05.md).
