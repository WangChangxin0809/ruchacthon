# CC Workbench

Local multi-agent workbench whose only engine is Claude Code: a main agent
plus background workers in git worktrees, artifacts with feedback, and a Room
for coordination. The surprising part: a same-workspace claim conflict is
deliberately escalated to a human and the worker stays blocked on that
decision, on purpose, per [ARCHITECTURE.md](ARCHITECTURE.md).

- **Covers**: rules that apply everywhere and cannot be enforced by a script.
- **Does not cover**: anything true of one directory only (that directory's own
  `CLAUDE.md`), anything a script can block (`scripts/guards/`), anything a
  script can detect (`scripts/gates/`). Detail added here is paid on every turn
  of every session, forever.

## Hard rules

1. A same-workspace claim conflict must escalate to a pending human decision and stay blocked until one is recorded -> [ARCHITECTURE.md](ARCHITECTURE.md)
2. Every model call goes through `backend/app/cc_runner.py`; no second model client, no second scheduler -> [docs/decisions/0003](docs/decisions/0003-claude-code-only-harness.md)
3. Secrets are referenced by environment-variable name only; never in a response, log, event, or DB row -> [ARCHITECTURE.md](ARCHITECTURE.md) invariant 5

## Commands

```bash
./ci.sh              # the single acceptance entry point
./ci.sh --fast       # what to run while working
```

## Where to look

- Bird's eye view and invariants: ARCHITECTURE.md
- Full routing table: docs/index.md

<!-- Cap: 100 lines, enforced by scripts/gates/check_context_budget.py.
     Hitting the cap is a signal to move a rule one hop out, not to compress it. -->
