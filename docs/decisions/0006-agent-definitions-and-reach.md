# 0006 — Agent definitions drive the run, and an agent's reach is its owner's

Date: 2026-09-06
Status: accepted; implements 0004 §3 and §4

## Context

0004 §4 designed `agent_definitions` as a row of Agent SDK options a person
can copy and edit, and §3 designed the tools one agent uses to reach another.
Building both raised two questions the design left open.

**Which prompt does a copy get?** 0004 has one `role` column
(`orchestrator | worker | any`) and four built-ins whose prompts differ
(prompts.py: orchestrator, worker, reviewer, chat). A copy of 审查者 has
`role = worker`, so `role` alone cannot say "render the reviewer prompt".

**Who may an agent talk to?** 0004 §3 says a tool call touching another
session is refused when the caller "is not a member of that session's
conversation". Taken literally no agent could ever message another, because
an agent is only ever a member of its own conversation, and the orchestrator
must be able to message the workers it spawns.

## Decision

**A second column, `prompt_role`.** `role` says where a definition may be
used (which session kinds may bind it); `prompt_role` says which of
prompts.py's four texts is rendered. Copy-to-create carries `prompt_role`
over unchanged, so a copy of the reviewer stays a reviewer no matter what the
person renames it. Only the four built-ins introduce a `prompt_role`; a
person picks a role and inherits the prompt of the built-in they copied.

**Reach follows the owner, not the project.** An agent speaks for the person
who started its run (`runs.created_by`), so it can address exactly the
sessions that person is a member of. Concretely, `runs._reachable` refuses
when the target is in another project, is the caller itself, or has a
conversation the run's owner does not belong to. Runs with no owner (single
user mode, legacy rows) are unrestricted within their project.

This keeps 0004's promise that a non-member sees nothing — one person's
worker cannot read or interrupt another person's — while letting the
orchestrator reach the workers it spawned, because `spawn_worker` copies the
spawning conversation's human members into the new session.

Refusals are AO's `sessionguard` shape: the tool returns a string starting
with `refused:` and says why, rather than raising or silently doing nothing.
`list_agents` filters by the same predicate, so the model is never shown a
name it would then be refused for using.

## Consequences

- Every run now gets the `room` server; the `workbench` server (spawn, kill,
  rename, list definitions) is added only when the definition has
  `can_spawn`. The system prompt lists exactly the tools the run has, so a
  reviewer is never told to call `spawn_worker`.
- `RunSpec` fields that used to be constants in runs.py (permission mode,
  max turns, tool allow/deny, effort, budget) now come from the definition,
  and `runs.agent_definition_id` records which one a run actually used.
- Rebinding a session while a run is live is refused with 409 rather than
  silently applying next time.
- A deleted definition resolves to the built-in for the session's kind and
  the session view says `missing: true`, so an old session keeps running and
  the UI can say why its name changed.
- `MAIN_SYSTEM` and `WORKER_SYSTEM` are gone from runs.py; prompts.py is the
  only place wording lives.
