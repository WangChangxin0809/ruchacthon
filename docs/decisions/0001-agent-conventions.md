# 0001 — This repository carries its own harness

Date: <YYYY-MM-DD>
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

- **A longer `CLAUDE.md`.** <Why: the cost is per-turn and unbounded, and the
  content had no reading trigger.>
- **<the other real alternative you considered>.** <Why not.>

Record the alternatives honestly. A decision that lists only the winner reads as
inevitable, and the next person re-proposes what was already rejected.

## Revisit when

<What would have to become true. Without this, the record becomes folklore of a
different kind — permanent instead of forgotten.>
