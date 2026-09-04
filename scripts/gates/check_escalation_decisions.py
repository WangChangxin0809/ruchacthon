#!/usr/bin/env python3
"""Gate: no merge while a team-scope claim conflict is undecided.

This is the enforcement point for the track's boundary rule -- remove the
human and the workflow must stop working, not just look worse. AgentRoom
resolves worktree/person scope conflicts on its own; team-scope conflicts
(different teammates' agents wanting the same file) are written to
backend/data/room_log.jsonl as a "CONFLICT escalated: <id>" claim entry.
Each such id must have a matching record in backend/data/decisions.jsonl
before this gate passes -- machine coordination stops at the team boundary,
a person picks up from there.

    0 = every team-scope conflict has a decision (or none exist, or the
        backend has never run -- there is nothing to gate on either way)
    1 = judged failure: at least one conflict is still undecided

Run standalone: python3 scripts/gates/check_escalation_decisions.py --root <repo>
"""
from __future__ import annotations

import argparse
import json
import os
import re

CONFLICT_RE = re.compile(r"CONFLICT escalated: (\S+)")


def _read_jsonl(path):
    if not os.path.exists(path):
        return None
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    log_path = os.path.join(args.root, "backend", "data", "room_log.jsonl")
    decisions_path = os.path.join(args.root, "backend", "data", "decisions.jsonl")

    log = _read_jsonl(log_path) or []
    if not log:
        print("check_escalation_decisions: no room_log.jsonl yet, nothing to gate on "
              "(run the backend and let agents claim files first)")
        return 0

    escalation_ids = set()
    for entry in log:
        if entry.get("type") != "claim":
            continue
        m = CONFLICT_RE.search(entry.get("note", ""))
        if m:
            escalation_ids.add(m.group(1))

    if not escalation_ids:
        print("check_escalation_decisions: no team-scope conflicts logged, nothing to gate on")
        return 0

    decisions = _read_jsonl(decisions_path) or []
    decided_ids = {d.get("escalation_id") for d in decisions if d.get("decision") == "approve"}

    undecided = escalation_ids - decided_ids
    if undecided:
        print(
            "Blocked: the following AgentRoom escalations are unresolved -- "
            "a human has not approved a decision for them, and this repo's "
            "gate requires one before merge (see backend/README.md):\n  "
            + "\n  ".join(sorted(undecided)),
        )
        return 1

    print(f"check_escalation_decisions: all {len(escalation_ids)} team-scope "
          f"conflict(s) have an approved human decision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
