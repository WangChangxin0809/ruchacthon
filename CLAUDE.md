# <project>

<One paragraph: what this is, and the one thing that is surprising about it.>

- **Covers**: rules that apply everywhere and cannot be enforced by a script.
- **Does not cover**: anything true of one directory only (that directory's own
  `CLAUDE.md`), anything a script can block (`scripts/guards/`), anything a
  script can detect (`scripts/gates/`). Detail added here is paid on every turn
  of every session, forever.

## Hard rules

1. <rule> -> <docs/path.md>

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
