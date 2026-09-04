# 0001 — This repository carries its own harness

Date: 2026-09-04
Status: accepted

## Context

Coding agents work here. Conventions written as prose in a single file were read
once per session, paid on every turn, and followed unevenly -- and the failures
were silent, because nothing distinguishes "the rule was followed" from "the
rule was never read".

The underlying constraint: knowledge only changes behaviour if it arrives at the
moment of acting. A repository has a fixed set of such moments, and each one has
a different cost and a different reach.

## Decision

Route every convention to the moment it is needed, and enforce mechanically
whatever can be enforced mechanically.

| Moment | Mechanism | Holds |
|---|---|---|
| Every turn | `CLAUDE.md`, capped | Rules with no local trigger |
| Session start | `SessionStart` hook | What is true only right now |
| Reading a subtree | nested `CLAUDE.md` | Rules local to one directory |
| Before an action | `scripts/guards/` | What review cannot undo |
| At CI time | `scripts/gates/` | Detectable states |
| On demand | `docs/`, skills | Everything else |

Consequences that follow, and are load-bearing:

- Knowledge lives in the repository, never in per-machine agent memory. Memory
  is invisible to review and cannot be corrected by a teammate.
- A rule that cannot tolerate a miss is never left to retrieval. Retrieval is
  best-effort by construction.
- Every check states, in its failure output, what to do and which document
  explains why. Failure output is the only text guaranteed to be read.

## Rejected

- **A longer `CLAUDE.md`.** Why not: the cost is per-turn and unbounded, and
  the content had no reading trigger — a rule buried in paragraph 40 is read
  exactly as often as one that was never written.
- **A single MCP process per agent instead of one shared server.** This is
  the AgentRoom-specific version of the same argument: a per-agent MCP
  process cannot see another agent's claims without its own IPC layer, which
  is a second harness alongside this one. Mounting the MCP app inside the
  same FastAPI process as the dashboard (see `backend/app/main.py`) means
  `RoomState` is the *only* place coordination state lives, enforced by
  Python's own object identity rather than a protocol we would have to keep
  in sync by hand.

## Revisit when

Real multi-tenant usage shows up (see `SECURITY.md`'s open item on
`/api/escalations/{id}/decide` having no auth), or the CRDT worktree-scope
merge needs to survive a backend restart (currently in-memory only, reset on
process restart by design — see `backend/app/room_state.py::RoomState.__init__`).
