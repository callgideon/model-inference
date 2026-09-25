# I8-INTAKE-PANELS — OB-10 follow-up for WR-I8-3 (a4efcf1c)

Base 130e948b (integration tip), head a4efcf1c (implementation commit; this evidence and the
update file are committed on top). Branch codex/i8-intake-panels. No docker beyond the
task-local i8 stand-in; no hosted DB, box, AWS or SSM.

## Problem
`tests/integration/backend/recovery/test_observe.py::test_i3b_ob10_every_rule_and_panel_names_a_declared_metric`
failed at 130e948b: `infrx_large_body_slots_in_use`, `infrx_large_body_slots_limit`,
`infrx_large_body_refused_total`, `infrx_intake_drained_total` (declared in
`infrx/observe/metrics.py`, recorded by `infrx/gateway/routes/intake.py`) had no panel.
Found alongside: operations.json `GatewayBodySlotsFull` read `infrx_large_body_in_use` /
`infrx_large_body_limit` (pre-WR-I8-3 names; nothing records them, so the rule could never
fire) and listed them in `pending_producers`; the I suite's `runtime_producers()` did not see
intake's `record(registry, "set", CONSTANT)` call sites.

## Changes
- `infra/alerts/dashboard.json`: row "Is the intake saturated? (gateway large-body slots,
  refusal drain)" with `runbook: infra/runbooks/observe.md#intake`; panels: slots in use vs
  limit (compare), refused (rate), drained by `code` (rate; closed 4-value vocabulary). No
  `process` split: gateway is the only producer (same as the in-flight panel).
- `infra/alerts/operations.json`: `GatewayBodySlotsFull` on the declared
  `infrx_large_body_slots_*` families, runbook `#intake`; stale names removed from
  `pending_producers`. Threshold unchanged (exact: at the limit). No new rule: refusal- and
  drain-rate thresholds are ⚠️ TO BE VERIFIED (P-25) in the runbook.
- `infra/runbooks/observe.md`: `## Intake` (slots pinned / leaked slot, refusals rising,
  the drain by code); the old one-liner under Edge removed; verification log appended.
- `apps/infrx-api/tests/i/test_observe.py`: `runtime_producers()` resolves module constants
  passed to `record(...)`; two cases (oracles in the file):
  `test_ops_intake__the_intake_row_shows_every_intake_family_on_closed_labels`,
  `test_ops_intake__the_slots_rule_fires_at_the_limit_only` (7/8 quiet, 8/8 fires).
- `apps/infrx-api/tests/i/mutants.py`: `intake_panel_missing`, `intake_vocabulary_widened`,
  `intake_rule_stale_family`.

## Commands (cwd apps/infrx-api unless noted)
| command | exit | result |
|---|---|---|
| `.venv/bin/python -m pytest -q ../../tests/integration/backend/recovery/test_observe.py` @130e948b | 1 | 1 failed (OB-10), 15 passed |
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_observe.py -k "ops_intake or without_a_producer"` before the fix | 1 | 2 failed (no intake row; rule reads stale names), 1 passed |
| same OB-10 command after | 0 | 16 passed |
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_observe.py` after | 0 | 18 passed, 1 xfailed (first try: stand-in busy, BlockingIOError at pooler.py:108; retried) |
| `uv run --frozen python tests/i/mutants.py intake_panel_missing intake_vocabulary_widened intake_rule_stale_family` | 0 | 3/3 killed |
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_mutants.py` | 0 | 50 passed |
| (repo root) `tests/integration/backend/recovery/mutants_i3b.py --only i3bm24` | 0 | 1/1 killed |

## Wiring requests
None.

## Open issues
- Refusal/drain-rate alert thresholds: ⚠️ TO BE VERIFIED (P-25), no rule until pilot traffic.
- Nothing applied on the box.

## Estimate
Remaining: optimistic 0 h, likely 0.25 h (coordinator review/merge), pessimistic 1 h;
confidence high; basis: all named suites green, dashboard/rules only. At 2026-09-25T17:44Z.
