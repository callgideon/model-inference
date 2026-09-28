# D7 follow-up — review minors, WR-B-2, WR-B-7, H1 DatasetSources (cut session) and WR-R3-2 variant comparisons (lab-sql, LW2)

- Base `eb0734d7` · follow-up `9b8cf423` (cut session, 0034) · WR-R3-2 `be40bda4` (0040) · code head `be40bda4` · branch `codex/w5-lab-sql-lw2`
- Task-local only (`INFRX_D_TASK=dlab`). LOCAL-ONLY migrations (R151). Nothing composed; no composition root touched.

## Changed paths (all owned)
- `0034_lab_eval_followup.sql` (cut session; reviewed, unchanged) and `0040_lab_variant_comparisons.sql`.
- `infrx/state/lab_data.py` (cut session: `finish(error=)`, `release`, `run_results`, `put_evaluator`/`evaluator`, `put_eval_report`/`eval_report`, `uses`; now + `put_variant_comparison`, `variant_comparisons`).
- `tests/d/test_d7_followup.py` (14), `test_d7_variant.py` (4), `test_d7_units.py` (8), `code_mutants_d7.py` (`FOLLOWUP` 36 + `VARIANT` 11 SQL, +3 Python), `test_code_mutants_d7.py`, `test_upgrade_lab.py`.

## The cut session's items (0034), as recorded then
F3 reaper SKIP LOCKED observed; F4 all-failed run fails; F5 a sample without its source refused; F6 revocation stops leasing mid-run; F7 per-provider source/checkpoint ids; RSI-2 rollback note corrected; RSI-3 cost unit of the run's budgets; WR-B-2 (a) error code (b) registered evaluators (c) results read (d) **402 lease release `lab_release_attempt`** (the next lease is the same attempt number and key); WR-B-7 write-once reports by `report_digest`; H1 `lab_dataset_uses` = the captured grant versions' scope (fail-closed), `PgLabDataStore.uses` composes with `LabAccess` (R172). Seam first: 14 errors without 0034 (`UndefinedFunction infrx.lab_put_evaluator`) -> 14 passed.

## WR-R3-2 (0040)
`lab_variant_comparisons`: one row per `sha256` of the stored RFC 8785 bytes of an `infrx.variant_comparison.1`; `variant_ref` must be a published `lab:variant` record of the provider and `report_digest` the provider's B2 report (`not_found`); `optimization_claimed` only with `equivalent` (CHECK); immutable; `lab_variant_comparisons {provider_org_id, report_digest}` reads them beside the report.

## Commands (`apps/infrx-api`, `INFRX_D_TASK=dlab`)
| command | head | exit | result |
|---|---|---|---|
| `pytest -q tests/d/test_d7_variant.py` WITHOUT 0040 (seam first) | pre-`be40bda4` | 1 | **4 failed** |
| `pytest -q tests/d/test_d7_variant.py tests/d/test_upgrade_lab.py` | `be40bda4` | 0 | **4 + 3 passed** (the upgrade: 0001-0026 history -> every Lab file; + 0027-0035 Lab rows -> 0036-0040 unchanged and re-runnable) |
| `INFRX_MUTANTS=all pytest -q tests/d/test_code_mutants_d7.py` | `be40bda4` | 0 | **148 passed** (run with D6F's list: 214 passed in 359 s): 67 + 36 + 11 SQL and 31 Python mutants killed, 3 list checks |

## Wiring
- WR-LSQ-1 (migration pin, D6J evidence). `tests/d/test_code_mutants_d7.py` is already on api-mutants line 1.
- WR-LSQ-6 (rollout-control lane, at merge): R3 stores its result with `PgLabDataStore.put_variant_comparison(result, provider_org_id=, actor=)` after B2's `put_eval_report`.

## Estimate (remaining, D7 follow-ups): 0.5/1.5/3 h, confidence medium; one review round.
