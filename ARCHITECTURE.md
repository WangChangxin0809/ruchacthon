# Architecture

- **Covers**: how this system works, for someone who does not yet know what to
  ask. Read on demand, so it may be long.
- **Does not cover**: how to perform a task (`docs/how-to/`), why a specific
  choice was made (`docs/decisions/`).

<!-- Write this by hand. A generated rollup describes the current accident; a
     written one describes the intent, including the constraints that are not
     visible anywhere in the code. That is the whole reason this file exists. -->

## What it does

<Two or three paragraphs. What problem, for whom, and the shape of the answer.>

## Codemap

| Directory | Holds | Talks to |
|---|---|---|
| | | |

## Invariants

Properties that must hold across the whole system. For each one, say where it is
enforced -- a property with no enforcement point is an aspiration, and naming it
here without one is how it quietly stops being true.

1. <invariant> — enforced by <scripts/gates/…, a type, a schema>

## Constraints that are not visible in the code

<Deadlines, a vendor API's rate limit, a platform quirk, a decision made for a
reason that no longer applies but is expensive to undo. This section is the one
a newcomer cannot reconstruct by reading, and the one that saves the most time.>
