<!-- unrouted: delivered automatically with the plan folder, per docs/index.md -->
# While this plan is in flight

- Branch: `feature/cc-workbench`.
- Do not add a second model-calling path; every model call goes through `backend/app/cc_runner.py`.
- Run status changes only via `RunManager` so the `run_status` event and the row never disagree.
- Proof for the current step: `python3 backend/tests/test_workbench.py`.
