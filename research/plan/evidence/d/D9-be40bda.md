# D9 — Release policies and stable assignment, plus R2's ReleaseStore (WR-R2-1) (lab-sql, LW2)

- Base `eb0734d7` · D9 `3d1b7c65` (cut session) · WR-R2-1 `f7155132` · code head `be40bda4` · branch `codex/w5-lab-sql-lw2`
- Task-local only (`INFRX_D_TASK=dlab`). LOCAL-ONLY migrations (R151). Nothing composes `PgLabRolloutStore` or `PgReleaseStore` (R1-R3 wire them); no admission hook changed.

## Changed paths (all owned)
- `apps/app/supabase/migrations/0033_lab_rollout.sql` (D9; in-place, unmerged: + state `approved`, live for the one-rollout-per-endpoint index, left by D9's expand/stop/rollback), `0039_lab_release.sql` (WR-R2-1).
- `apps/infrx-api/infrx/state/lab_rollout.py`: `PgLabRolloutStore` (D9) + `PgReleaseStore`, `Release` (R2's `ReleaseStore`/`Release` field for field).
- `apps/infrx-api/tests/d/test_d9_rollout.py` (9), `test_d9_release.py` (7), `test_d9_units.py` (2), `code_mutants_d9.py` (`SQL_MUTANTS` 35, `RELEASE` 23, `CODE_MUTANTS` 10), `test_code_mutants_d9.py`, `test_upgrade_lab.py`.

## ONE schema: R2's release IS D9's rollout row
- `release(policy_ref) -> Release(state, fence, plan_digest, started_at)`: the `lab_rollouts` row at that revision; `started_at` = its `start` event; `plan_digest` new column, written once by `lab_release_start` (D9's `lab_rollout_start` + the digest in one transaction; a trigger freezes it).
- `transition(policy_ref, fence, to, decision, reasons) -> new fence`: CAS on the SAME fence D9's own moves bump (a stale fence is `state_conflict`); R2's moves only (running -> approved | rolled_back, approved -> rolled_back); the decision is `records.parse`d in Python (LabRejected before any SQL) and checked again in SQL (this provider, this revision, `expand` for approved with the provider's run records as evidence, `rollback` for rolled_back); stored append-only in `lab_rollout_events` (+ `decision_doc`, `reasons`).
- `approved` (R2's operator-approved expansion) is live (no second policy on the endpoint), routed to the BASELINE by `lab_rollout_assign` (only `running` buckets; WR-R2-3's rule), and left by D9's `expand` to the later version (running), `stop` or `rollback`.

## Commands (`apps/infrx-api`, `INFRX_D_TASK=dlab`)
| command | head | exit | result |
|---|---|---|---|
| (D9, cut session) seam / checks / mutants | `3d1b7c65` | 1 -> 0 | 9 failed without 0033 -> 9 passed; 44 passed (35 SQL + 6 Python) - rerun below |
| seam first without 0036-0039 (see D6J evidence) | pre-`f7155132` | 1 | `test_d9_release.py`: every check failed but the (then vacuous) role probe |
| `pytest -q tests/d/test_d9_release.py` first runs | pre | 1 | apply error (PL/pgSQL IF ends at the CASE's THEN: parenthesized); then 3 failed: `lab_release_start` updated with the start call in its WHERE (volatile, per row, invisible insert) - now called first |
| `pytest -q tests/d/test_d9_release.py tests/d/test_d9_rollout.py tests/d/test_d9_units.py` | `f7155132` | 0 | **7 + 9 + 2 passed** |
| `INFRX_MUTANTS=all pytest -q tests/d/test_code_mutants_d9.py` | `f7155132` | 0 | **71 passed**: 35 + 23 SQL and 10 Python mutants killed, 3 list checks (every case named) |

## Oracles and drills (brief)
ROLLOUT-PIN: repeat assignment identical (200 SQL assignments = `records.assign`; retries identical across expansion, approval and rollback); explicit pins honoured (also once stopped); an approved release routes new requests to the baseline while admitted ones keep theirs. ROLLOUT-RECOVER: stale fence refused (D9 and R2 moves share the fence); two publishers racing (4 at one D9 fence -> one decision; 2 R2 controllers at one fence -> one rollback, one `state_conflict`); two policies started on one endpoint -> one live (approved counts as live).

## Wiring requests
- WR-LSQ-1 / WR-LSQ-2 (D6J evidence): the migration pin and `tests/d/test_code_mutants_d9.py` on the own-process PG line.
- WR-LSQ-5 (rollout-control lane, at merge): compose `PgReleaseStore(connect)` as R2's `ReleaseStore` (its `Release` is R2's field for field) and rerun `tests/r/control` with it on key r2; the launcher uses `PgReleaseStore.start(policy_ref, provider_org_id=, plan_digest=plan_digest(plan), decided_by=, reason=)`.

## Open issues
- WR-R2-1 addendum (D9 could return the stored policy): not done - R2 verifies the caller's policy against the ref, and the policy body is `lab_records` (readable through `PgLabDataStore.resolve`).

## Estimate (remaining, D9 incl. WR-R2-1): 1/2/4 h, confidence medium; basis: one review round plus R2's merge-time swap of its fake.
