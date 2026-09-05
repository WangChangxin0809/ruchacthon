# docs/ routing table

- **Covers**: mapping "what I am about to do" to "what to read, then where to edit".
- **Does not cover**: the knowledge itself — that lives in the document being
  pointed at. Detail written back into this table is paid by every reader who
  did not need it.

## Directories, partitioned by why you opened them

| Directory | You are here because | Shape |
|---|---|---|
| `how-to/` | I need to do a thing | Ordered steps: action → command → criterion |
| `reference/` | I need to look up a fact | Tables and rules, keyed for lookup |
| `decisions/` | Why is it like this? | Numbered from the PR, superseded not edited |
| `exec-plans/` | What are we in the middle of? | One folder per plan: `README.md` owns state, `steps/` owns substance |

**This top level is fixed; inside each directory, organise however suits the
material.** `scripts/gates/check_docs_layout.py` holds the top level and checks
nothing below it. A directory that forks a required name — `adr/`, `howto/`,
`plans/` — is an error even when routed, because two spellings of one bucket
both accumulate documents and merging them later is a migration. Additions are
fine once a row below routes into them.

Two things are deliberately not directories. **A symptom and its fix belong in
the failure output** of the guard or gate that detects it, not in a file nobody
opens while stuck. **Generated is a property**: such a file lives where its
content belongs and declares its source in its own first line, and the gate is
that regenerating leaves an empty `git diff`.

A plan past a few steps is a folder, not a file. `README.md` carries the goal,
the abort condition, and one row per step — **milestones, not a task list**.
What the current step breaks into for the next ten minutes belongs in the
runtime's own todo list, which is live in the interface and vanishes with the
session; the README holds what outlives it. A step earns its own file under
`steps/` only when it has decisions to record. Step files never restate status —
nobody reopens a finished one to change `doing` to `done` — and each opens with
`## Consulted` saying what was searched before the work started, or why nothing
was. The routing table below points at the `README.md` only.

An **active** plan folder also carries a `CLAUDE.md`, capped at 50 lines by
`scripts/gates/check_context_budget.py`. It is delivered whenever anything in
that folder is read, so it holds only what is true *while this plan is in
flight* — the invariant not to break, the branch, the one command that proves a
step landed. Not the plan: that is the `README.md` next to it, and restating it
here means two copies drifting apart in the one place where being out of date is
most expensive. Delete the file when the plan finishes; a stale one is worse
than none, because it is still being delivered — `scripts/gates/check_plan_hygiene.py`
fails on one, and `scripts/context/on_stop.py` runs it at the end of every turn
rather than waiting for CI, because that is how often the cost is paid.

## I want to X -> read Y -> then edit Z

| I want to | Read first | Then edit |
|---|---|---|
| Understand the system | [ARCHITECTURE.md](../ARCHITECTURE.md) | — |
| Know why the repo is shaped this way | [0001](decisions/0001-agent-conventions.md) | — |
| Know why Claude Code is the only engine, and what was probed | [0003](decisions/0003-claude-code-only-harness.md) | — |
| Build the multi-user, multi-agent milestone (users, teams, conversations, agent definitions, vendor keys, proxy) | [0004 track3 design](decisions/0004-track3-multi-user-multi-agent.md) | per its §8 batches |
| Know why users, teams and one conversation model exist, and what was rejected | [0005](decisions/0005-users-teams-conversations.md) | — |
| Change what an agent is allowed to do, or who it may talk to | [0006](decisions/0006-agent-definitions-and-reach.md) | `backend/app/agents.py`, `backend/app/runs.py` |
| Add a route that reads or writes on somebody's behalf | [ARCHITECTURE.md](../ARCHITECTURE.md) invariant 7 | `backend/app/teams.py` (visibility), `routes_*.py` |
| Know why the old hand-written model loop existed (historical) | [0002](decisions/0002-llm-client-abstraction.md) | — |
| See what a real Claude Code run proved, and what is still unverified | [acceptance 2026-09-05](reference/acceptance-2026-09-05.md) | — |
| Add a worker tool or change what the main agent can do | [ARCHITECTURE.md](../ARCHITECTURE.md) codemap | `backend/app/runs.py` (`_main_tools` / `_room_tools`) |
| Add a provider path | [ARCHITECTURE.md](../ARCHITECTURE.md) invariant 5 | `backend/app/providers.py` (`KINDS`, `env_for`) |
| Reach the Aliyun server | [connect-to-aliyun-server](how-to/connect-to-aliyun-server.md) | — |
| Deploy the workbench to the server | [deploy-aliyun](how-to/deploy-aliyun.md) | `scripts/deploy/` |
| Look at Agent Orchestrator side by side | [compare-with-ao](how-to/compare-with-ao.md) | `scripts/deploy/ao-compare.sh` |
| See what is in flight | [cc-workbench plan](exec-plans/cc-workbench/README.md), [tech debt](exec-plans/tech-debt-tracker.md) | — |
| Deploy / SSH into the server | [connect to aliyun server](how-to/connect-to-aliyun-server.md) | — |
