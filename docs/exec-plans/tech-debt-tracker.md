# Tech debt tracker

- **Covers**: defects and shortcuts found in passing, with enough detail to act
  on later.
- **Does not cover**: work that is in flight — that gets its own file in this
  directory.

Something found while doing something else goes here rather than being fixed
inline. A batch that grows while you work is a batch that never lands, and a
fix bundled into an unrelated change is a fix nobody reviewed.

Each entry carries the reading that revealed it and the commit it was measured
on. A reading without a commit expires silently, and whoever inherits it
restarts from a number that stopped being true weeks ago.

| Found | Commit | What | Blast radius | Reading |
|---|---|---|---|---|
| 2026-09-05 | feature/cc-workbench | `LICENSE` still missing; `check_community_health.py` stays red until the team picks one | public face, ci.sh exit 1 | gate output |
| 2026-09-05 | feature/cc-workbench | Docker files updated for the CC engine but never built; needs `claude` login inside the container | anyone using compose | ARCHITECTURE.md "not enforced" |
| 2026-09-05 | feature/cc-workbench | Shared-edit mode cannot see Bash/editor writes that land during a merge; labelled experimental | shared workspaces only | `shared_edit.py` docstring |
| 2026-09-05 | feature/cc-workbench | The SDK spawns its bundled `claude`, not the one on PATH; discovery reports the PATH one. Pass `cli_path` if they diverge | version skew | probe output |
| 2026-09-05 | feature/cc-workbench | Merge commits whatever the worktree holds (`git add -A`), including `__pycache__` when the project has no `.gitignore` | merged branches | acceptance run 11 |
| 2026-09-05 | feature/cc-workbench | Board shows 5 columns in a 460px panel and scrolls horizontally; should wrap | UI | screenshot |
