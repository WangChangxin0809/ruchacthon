# AgentRoom

When several Claude Code agents edit the same repository at once, the collision
that matters is not the character-level kind (CRDTs already solve that) -- it
is two *teammates'* agents both deciding to own the same file, which nobody
should let a machine settle unilaterally. AgentRoom auto-merges the first kind
and escalates the second to a human, then blocks the merge until that human
has actually decided.

- **Covers**: what this is, how to run it, and where to go next.
- **Does not cover**: how to work *on* it (CONTRIBUTING.md), how the pieces fit
  (ARCHITECTURE.md), how to perform a task (docs/how-to/).

## Quick start

```bash
# backend
python3 -m pip install --user --break-system-packages -r backend/requirements.txt
python3 -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8787 &

# frontend
cd frontend && npm install && npm run dev -- --host 0.0.0.0 --port 5173 &

# demo: two agents racing to claim the same file
python3 scripts/demo/run_two_agents.py
```

If it worked: the dashboard at `http://localhost:5173` shows both agents,
one pending escalation for the file they both claimed, and
`python3 scripts/gates/check_escalation_decisions.py` exits 1 until you
click "通过" in the dashboard, after which it exits 0.

## Requirements

- Python 3.11+, Node 20+

## Documentation

- Bird's eye view and invariants: [ARCHITECTURE.md](ARCHITECTURE.md)
- Everything else, routed: [docs/index.md](docs/index.md)
- Backend details: [backend/README.md](backend/README.md)
- Frontend details: [frontend/README.md](frontend/README.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues go to
[SECURITY.md](SECURITY.md), not the issue tracker.

## License

Unlicensed for now — a hackathon submission under Fresh Build rules; see
[SECURITY.md](SECURITY.md) for the placeholder note on this.
