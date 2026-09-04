# Security

- **Covers**: how to report a vulnerability, and what this project treats as one.
- **Does not cover**: the rules themselves. Those are not prose, because prose
  is not enforcement:
  - what must never leave the machine → `scripts/guards/`
  - what must never enter the tree → `scripts/gates/`
  - why the boundary is drawn where it is → `docs/decisions/`

## Reporting

<Where to send it, and what response time to expect.>

## Threat model

The model itself belongs in a decision record, because it is a choice with
alternatives and it will be revisited. Link it here once written:

- `docs/decisions/00NN-threat-model.md`

## What is enforced, and where

| Rule | Enforced by |
|---|---|
| No credentials in the tree | `scripts/gates/` |
| No secrets piped to an outbound command | `scripts/guards/no_piped_outbound.py` |
