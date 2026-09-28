# D6J — Lab consent, USD budgets and submission coordination, plus the judge ledger (SR-J2-1) and the Lab judge doors (SR-C3L-1) (lab-sql, LW2)

- Base `eb0734d7` · D6J `759275e9` (cut session) · SR-J2-1 + SR-C3L-1 `0a77ab3e` · code head `be40bda4` · branch `codex/w5-lab-sql-lw2`, worktree `.claude/worktrees/codex-w5-lab-sql-lw2` (continuation relaunch 2026-09-28; the first implementer was cut before checks/evidence)
- Task-local only: `INFRX_D_TASK=dlab` (PostgreSQL 57500, decoy 27500). LOCAL-ONLY migrations (R151), none applied hosted; 0001-0030 untouched. Every write here is behind D6J's flag `lab_submission` (no row = OFF): nothing reachable from the launched App/API changes. The C3L doors are `authenticated` functions in `public` and ALSO require the flag, so the launched App's database answers 55000 until the Lab is enabled.

## Changed paths (all owned)
- `apps/app/supabase/migrations/0031_lab_consent.sql` (D6J, cut session; reviewed, unchanged), `0036_lab_judge_ledger.sql` (SR-J2-1), `0037_lab_judge_doors.sql` (SR-C3L-1).
- `apps/infrx-api/infrx/state/lab_consent.py`: `PgLabConsentStore` (D6J) + `PgJudgeLedger`, `JudgeRun`, `Consent` (J2's `JudgeLedger`, `LedgerRun`, `ConsentRef` field for field; duck-typed because `infrx.judge` is on the tip, not this base).
- `apps/infrx-api/tests/d/test_d6j_consent.py` (11), `test_d6j_judge.py` (11), `test_d6j_doors.py` (6), `test_d6j_units.py` (3), `code_mutants_d6j.py` (`SQL_MUTANTS` 39, `JUDGE` 32, `DOORS` 22, `CODE_MUTANTS` 15), `test_code_mutants_d6j.py`, `test_upgrade_lab.py`.

## ONE schema for J2's ledger and D6J's budgets (the requested mapping)
| SR-J2-1 asked for | implemented as |
|---|---|
| `lab_judge_budgets(payer_ref, provider_org_id, limit_value, unit)` | **0031's `lab_budgets`** (one row per provider + `lab:payer` ref, PROVIDER_USD only, versioned limits in `lab_budget_limits`). A judge run's hold goes into the same `reserved`, its actual into `settled`, so a payer's judge runs and external submissions share ONE cap; the CHECK `reserved + settled <= limit` serializes concurrent reservations. SR-C3L-1's `lab_judge_set_budget` writes this row through 0031's `lab_put_budget`. |
| committed = Σ held reserved + Σ completed actual | `lab_budgets.reserved + settled` (moved in the same transaction as each run move) |
| `lab_judge_runs` states | the frozen `external_run` machine: 0031's `lab_submission_may` + `lab_submission_guard` trigger reused on `lab_judge_runs` |
| `lab_judge_runs`, `lab_judge_results`, `lab_judge_audit` | as asked (+ `label_id` on results: C3L's calibration keyset) |
| RPCs reserve/begin_submit/record_sent/record_submission/quarantine/release/record_results/settle/run | `infrx.lab_judge_*`, one per port method |
| sweep (open issue 2) | `lab_judge_sweep {older_than_s}`: `submitting` untouched that long -> `ambiguous`, audited `lease_expired` |
| settle overrun "refuse and audit" (open issue 5) | the audit row is COMMITTED and the RPC answers `{refused: budget_exceeded}`; the adapter raises `BudgetExceeded`; the run stays `submitted` for an operator |
| consent snapshot | the grantor's CURRENT grant version to THIS provider, in force, `external_judging` over `request_content` AND `response_content` - at reserve and again at `record_sent` (immediately before egress): a revocation between consent and submit is `consent_missing` there even if J2's own L2 recheck raced it |
| D6J's `lab_submission` flag | gates every judge write and the four doors (J2's `JUDGE_MODE=live` stays the worker's own switch) |

SR-C3L-1 doors (`public`, SECURITY DEFINER, identity only `auth.uid()`, clock `infrx.now()`): refusals 42501 (no current membership at the role, no current grant naming the model over both categories for external_judging, a payer of another provider), P0002 (another provider's run id or configuration), 22023 (a bad size/unit), 55000 (flag off). `lab_judge_request_run` is idempotent on the run id, also under a concurrent double click; calibration = the provider's own runs' `lab_judge_results` labels, keyset on `label_id`, clamped to 50.

## Commands (`apps/infrx-api`, `INFRX_D_TASK=dlab`)
| command | head | exit | result |
|---|---|---|---|
| (D6J, cut session) seam first / `test_d6j_consent.py` / its mutants | `759275e9` | 1 -> 0 | 11 errors without 0031 -> 13 passed; 51 passed (39 SQL + 9 Python) - the first implementer's record, rerun below |
| `pytest -q tests/d/test_d6j_judge.py tests/d/test_d6j_doors.py tests/d/test_d6f_doors.py tests/d/test_d9_release.py` WITHOUT 0036-0039 (seam first) | pre-`0a77ab3e` | 1 | **25 failed, 1 passed** (the one pass, d9 release's role probe, was vacuous: it now also asserts the platform role reaches `lab_release`) |
| `pytest -q tests/d/test_d6j_judge.py` first run | pre | 1 | collection-time TypeError in the grant helper (duplicate kwarg) - fixed; then 11 passed |
| `INFRX_MUTANTS=all pytest -q tests/d/test_code_mutants_d6j.py` first run | pre | 1 | 21 failed: 15 Python = the runner function lost in an edit (restored); 5 door mutants = apply errors from 0039 being written mid-run; 1 survivor `d6jj_release_any_state` (the explicit move check duplicated the state trigger: removed; replaced by `d6jj_release_to_any_state` + `d6jj_release_undeclared`) |
| same | `0a77ab3e` | 0 | **111 passed**: 39 + 32 + 22 SQL and 15 Python mutants killed, 3 list checks |
| `ruff check` changed Python | be40bda4 | 0 | all checks passed |
| FINAL (focused + api-test): see "Final sweep" below | be40bda4 | | |

## Oracles and drills (brief)
- JUDGE-BUDGET: concurrent reservations over budget (4 x 40 against 100 -> exactly 2 held, 80 reserved; D6J's and the judge ledger's); duplicate submit under contention (4 racing `begin_submit` -> one `created`; 0031's: one intent, 3 `ambiguous_submission`); an unknown outcome quarantined with its hold, reconciled by lookup (submitted) or released (failed), never resubmitted; settle once, within the reservation.
- LAB-ACCESS: revocation between consent and submit (0031: `consent_missing` at the intent; 0036: `consent_missing` at `record_sent`, swept to ambiguous, released); old/narrower/other-purpose/other-provider/revoked grants refused at reserve; role matrix on the doors = 2 consumers (C1; BOTH), 2 providers (NEMO developer/administrator/viewer; OTHER developer = BOTH), 1 user in both products (BOTH): consumer-only, other provider, viewer, revoked member, anon all 42501.
- JUDGE-SCORES: one result per (run, sample, rubric version); a result for a sample that never left is refused.

## Wiring requests (lab-sql, this lane)
- **WR-LSQ-1** `tests/integration/test_harness.py` pin after `"0030_lab_access_self.sql",`:
  `# Lab (local-only, R151): lab-sql LW2 (D6J, L3-SQL, D9, D7/D6F follow-ups, the LW2 schema requests)` then
  `"0031_lab_consent.sql", "0032_lab_control.sql", "0033_lab_rollout.sql", "0034_lab_eval_followup.sql", "0035_feedback_scrub.sql", "0036_lab_judge_ledger.sql", "0037_lab_judge_doors.sql", "0038_feedback_doors.sql", "0039_lab_release.sql", "0040_lab_variant_comparisons.sql",`
- **WR-LSQ-2** `Makefile` api-mutants second (own-process PG) line: append `tests/d/test_code_mutants_d6j.py tests/d/test_code_mutants_l3sql.py tests/d/test_code_mutants_d9.py` (d6f and d7 are already on line 1; they now also carry the DOORS/SCRUB and FOLLOWUP/VARIANT lists).
- **WR-LSQ-3** `tests/d/checks.py EXPECTED_FUNCTION_CALLERS`, after the 0030 line:
  `"public.lab_judge_configure(uuid,uuid,uuid,text,integer,integer)"`, `"public.lab_judge_set_budget(uuid,text,jsonb)"`, `"public.lab_judge_request_run(uuid,uuid,uuid,text)"`, `"public.lab_judge_calibration(uuid,uuid,integer)"` (0037) and `"public.submit_feedback(jsonb)"`, `"public.lab_review_feedback(jsonb)"` (0038): each `{"authenticated", "service_role"}`. No `FILLED_BOUNDARIES` change: nothing here redefines a 0001-0026 object (0004's closed D6 stubs `reserve_judge`/`record_submission` are left as they are).
- **WR-LSQ-4** (judge lane, at merge) compose `PgJudgeLedger(connect)` as J2's `JudgeLedger` (its `JudgeRun` is `LedgerRun` field for field; `infrx.judge.submit` may alias it) and rerun `tests/j/submit` with the ledger swapped, the `lab_submission` flag on in the PG world; C3L's `judge.test.ts` can run against 0037 as is (names and codes are SR-C3L-1's).

## Open issues
- 0004's `infrx.reserve_judge`/`infrx.record_submission` stubs (D6, feature_not_supported) stay closed and unused; propose a ruling to retire them (a later migration) rather than fill them, since the ledger is named per SR-J2-1.
- `lab_judge_request_run` queues a request row; turning it into `lab_judge_reserve` is J2's worker (WR-J2-3, deferred).
- `PgJudgeLedger.record_results` stores a result whole (`dataclasses.asdict`); accepted = it has `scores` (J2's `JudgeScores`), else a `Rejected`.

## Proposed ruling (coordinator numbers)
- Lab judge ledger: judge runs and external submissions of a payer draw on ONE PROVIDER_USD budget row (0031 `lab_budgets`); a judge run's consent is the grantor's current `external_judging` grant version over request and response content, checked at reservation and again when the samples leaving are recorded; an overrun is refused with a committed audit row and the run left `submitted`.

## Estimate (remaining, D6J incl. SR-J2-1/SR-C3L-1): 1/2.5/5 h, confidence medium; basis: one review round (D10-0025 1/2/5) plus the judge lane's merge-time rerun over the real ledger.

## Final sweep (code head `be40bda4`, all five lab-sql LW2 tasks; `INFRX_D_TASK=dlab`)
| command | exit | result |
|---|---|---|
| `INFRX_D_TASK=dlab make api-test` (detached; `make` itself was SIGTERMed at ~95% - "make: *** [Makefile:13: api-test] Terminated", as in the L3 lane's run - the pytest child ran to completion into the same log) | 1 (pytest) | **5475 passed, 77 skipped, 9 xfailed, 9 failed** in 3714 s. The 9: (a) 2 = WR-LSQ-3, expected until wired: `test_credit_schema.py::test_credit_privileges__service_reads_money_and_writes_through_seams` and `test_schema_postgres.py::test_dur_rls__the_execute_surface_is_enumerated` both list exactly the six new door signatures (`authenticated may execute public.lab_judge_calibration(uuid,uuid,integer)`, `...lab_judge_configure(uuid,uuid,uuid,text,integer,integer)`, `...lab_judge_request_run(uuid,uuid,uuid,text)`, `...lab_judge_set_budget(uuid,text,jsonb)`, `...lab_review_feedback(jsonb)`, `...submit_feedback(jsonb)`) - nothing else; (b) 7 = `tests/d/test_outbox_relay.py[valkey]`: `ForeignContainer: infrx-d2-valkey exists and is not this checkout's` (another checkout's container, up 9 h; not touched). Every lab-sql PG suite ran on 57500 and passed; no `tests/i` failure this time. |
| rerun alone: `INFRX_D_TASK=dlab pytest -q tests/d/test_outbox_relay.py` | 1 | 13 passed, 7 failed - the same foreign `infrx-d2-valkey` still holds the name; for the coordinator to rerun when free |
| mutant lists (above, per task) | 0 | l3sql 49, d6j 111, d9 71, d7 148 + d6f 66 (one 214 run): every SQL and Python mutant killed, every case named |
| `ruff check` (24 changed Python files) | 0 | all checks passed |
| `git status` | - | clean apart from this evidence; the L3 probe copies were removed; `infrx/state/catalog.py` untouched (`git diff` empty) |
