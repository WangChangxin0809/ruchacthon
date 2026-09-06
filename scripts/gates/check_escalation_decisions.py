#!/usr/bin/env python3
"""Gate: no merge while a claim conflict is waiting on a human.

CLAUDE.md hard rule 1: a same-workspace claim conflict becomes a pending
human decision and stays blocked until one is recorded. The workbench
enforces that at runtime (backend/app/room.py::Room._conflict); this gate is
the second half -- nothing ships while a decision is still pending, so
"remove the human and the workflow stops" holds at the merge line too.

Reads the workbench SQLite database (backend/data/workbench.db by default,
or $WORKBENCH_DATA_DIR/workbench.db).

    0 = no pending decisions (or no database yet: nothing to gate on)
    1 = judged failure: at least one decision is pending

Run standalone: python3 scripts/gates/check_escalation_decisions.py --root <repo>
"""
from __future__ import annotations

import argparse
import os
import sqlite3


def db_path(root: str) -> str:
    data_dir = os.environ.get("WORKBENCH_DATA_DIR") or os.path.join(root, "backend", "data")
    return os.path.join(data_dir, "workbench.db")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    path = db_path(args.root)
    if not os.path.exists(path):
        print("check_escalation_decisions: no workbench.db yet, nothing to gate on "
              "(run the backend and let workers claim files first)")
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            rows = conn.execute("SELECT id, subject, blocked_run_id, created_at FROM decisions WHERE status = 'pending'").fetchall()
        except sqlite3.OperationalError as e:
            print(f"check_escalation_decisions: cannot read decisions table ({e})")
            return 2
    finally:
        conn.close()
    if not rows:
        return 0
    print(f"Blocked: {len(rows)} claim conflict(s) still waiting on a human decision:")
    for id_, subject, run_id, ts in rows:
        print(f"  {id_}  run {run_id}  since {ts}  {subject[:120]}")
    print("Decide each in the workbench Room panel (or POST /api/decisions/<id>/decide) before merging.")
    print("Why: CLAUDE.md hard rule 1 / ARCHITECTURE.md invariant 1.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
