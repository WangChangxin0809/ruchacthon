# Security

- **Covers**: how to report a vulnerability, and what this project treats as one.
- **Does not cover**: the rules themselves. Those are not prose, because prose
  is not enforcement:
  - what must never leave the machine → `scripts/guards/`
  - what must never enter the tree → `scripts/gates/`
  - why the boundary is drawn where it is → `docs/decisions/`

## Reporting

Open a GitHub issue on this repo, or DM `@WangChangxin0809`. Best-effort
response during the hackathon window; no SLA beyond that yet.

## Threat model

Not yet written as its own decision record — this is a 2.5-day hackathon
build, not a deployed multi-tenant service. The one thing worth stating now:
the dashboard's `/api/escalations/{id}/decide` endpoint has no auth, because
this MVP assumes a single trusted team on a single sandbox. That is the
first thing to fix before this runs anywhere with real access control at
stake.

## What is enforced, and where

| Rule | Enforced by |
|---|---|
| No credentials in the tree | `scripts/gates/` |
| No secrets piped to an outbound command | `scripts/guards/no_piped_outbound.py` |
