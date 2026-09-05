"""Pure state machines for the three independent axes of state (D3):
execution status, human review, and merge outcome. No IO -- callers in
exec/ and api/ hold the SQLite row and pass its current status in.

A process exiting cleanly answers "did the run finish" (RUN_STATUSES), not
"is the result good" (REVIEW_STATUSES) or "is it in the target branch yet"
(MERGE_STATUSES). Conflating these was the original design's bug.
"""
from __future__ import annotations

RUN_STATUSES = {
    "queued", "starting", "running", "cancelling",
    "succeeded", "failed", "cancelled", "budget_exhausted", "interrupted",
}

RUN_TERMINAL = {"succeeded", "failed", "cancelled", "budget_exhausted", "interrupted"}

# Which statuses a run may move to from a given status. Terminal statuses
# have no outgoing edges -- once a run is done, it is done; a retry is a
# new Run row (attempt+1), never a resurrected old one.
RUN_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"starting", "cancelling", "failed", "interrupted"},
    "starting": {"running", "cancelling", "failed", "interrupted"},
    "running": {"cancelling", "succeeded", "failed", "cancelled", "budget_exhausted", "interrupted"},
    "cancelling": {"cancelled", "failed", "interrupted"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
    "budget_exhausted": set(),
    "interrupted": set(),
}

REVIEW_STATUSES = {"unreviewed", "changes_requested", "approved"}
MERGE_STATUSES = {"not_merged", "merged", "conflicted", "not_applicable"}


class InvalidTransition(ValueError):
    pass


def validate_run_transition(current: str, new: str) -> None:
    if current not in RUN_TRANSITIONS:
        raise InvalidTransition(f"unknown run status: {current!r}")
    if new not in RUN_TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"run cannot go from {current!r} to {new!r}")


def is_run_terminal(status: str) -> bool:
    return status in RUN_TERMINAL
