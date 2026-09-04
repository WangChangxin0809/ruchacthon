# Contributing

- **Covers**: how to get a change accepted here — setup, the checks, and what a
  reviewer will look for.
- **Does not cover**: what the project is (README.md), how it works
  (ARCHITECTURE.md), why it is shaped this way (docs/decisions/).

## Before you open a pull request

```bash
./ci.sh --fast     # seconds; run this while working
./ci.sh            # everything; run this before pushing
```

Exit 2 is not a pass. It means a check could not judge — a missing tool, an
unparseable config — and it must be fixed rather than retried.

## What a reviewer checks

1. <the thing that actually gets changes sent back here>
2. Any new rule is enforced, not documented: an action a script can block goes
   to `scripts/guards/`, a state a script can detect goes to `scripts/gates/`.
3. A new check has been watched failing. `scripts/gates/selftest.py` proves it
   can turn red; a check nobody has seen fail is a file, not a check.

## Setup

```bash
<clone, dependencies, and the one environment thing people get wrong>
```
