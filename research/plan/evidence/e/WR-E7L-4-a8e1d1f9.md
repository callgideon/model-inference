# WR-E7L-4 - rebind lab_world.py to D8's real ledgers, plus the E7L gate rerun (lane lab-improve-2, LW6)

Base `1d26fd59`; branch `codex/w5-lab-improve-2`; code head `a8e1d1f9` (WR-E7L-5's fix is
`8609d0a2`/`a3389378`, on the same branch below this one). Tasklocal key `e7l` (compose
57300-57399, PostgreSQL mirror 57332) for the gate reruns; torn down after each run, nothing
left. Nothing touched the pilot box, hosted Supabase, AWS/SSM/S3, Vercel or a secret; no
migration, `apps/lab/**` or Makefile file changed; `infrx/**` untouched (D8's adapters already
existed at `infrx/state/lab_pipeline.py`, merged by an earlier lane - this item only rebinds the
*test world* to them).

## The rebind

`lab_world.py`'s `Lab.__init__` built D8's `LabelLog`/`RunLedger` and P2's teacher ledger from
`tests/p`'s in-memory stand-ins (`FakeLabelLog`, `FakeRunLedger`, `tests/p/teachers/fakes.
TeacherLedger`), even though `infrx/state/lab_pipeline.py`'s real `PgLabelLog`/`PgRunLedger`/
`PgTeacherLedger` (over `0042_lab_d8_ledgers.sql`) already existed, uncomposed anywhere
(confirmed: `grep -rn "PgLabelLog\|PgRunLedger\|PgTeacherLedger" apps/infrx-api/infrx` outside
`lab_pipeline.py` itself returns nothing before this change). Swapped:

* `self.log = PgLabelLog(connector(self.dsn))`, `self.runs = PgRunLedger(connector(self.dsn))`.
* `self.judge = PgTeacherLedger(connector(self.dsn))` (extends J2's real `PgJudgeLedger`).
* Enabled the `lab_submission` feature flag (parallel to D6F's own `feedback` flag already
  enabled here) - both `PgRunLedger`/`PgTeacherLedger` raise `DependencyUnavailable` otherwise
  (0031's rule).
* Seeded an operator profile (`insert into auth.users` + `update profiles set is_operator =
  true`), matching `tests/p/training/test_training_services.py`'s own real-PG pattern for
  WR-P3-R184 exactly (same shape, a fresh session-scoped id via `checks.USER_OPERATOR`, added as
  `self.OPERATOR`).
* Seeded the session payer's PROVIDER_USD budget through D6J's real `lab_put_budget` RPC
  (`PgLabConsentStore.put_budget`) instead of constructing a `ProviderUsd` value that only the
  fake ever read.

## Read from D8's tables, not a fake's internals (check 1: fail-first at the seam)

The real adapters expose only their typed port methods (`get`/`move`/`reserve`/`settle`/
`release`/`note`/`noted`), never a `.reservations`/`.spent`/`.committed(...)` dict the way the
fakes did. Every scenario assertion that reached into those fake internals broke immediately
against the real adapters - the exact failing seam, recorded before the fix:

| site (old) | failure against the real adapter | fix |
|---|---|---|
| `lab.runs.reservations[held]["state"]` (i06, x2) | `AttributeError: 'PgRunLedger' object has no attribute 'reservations'` | `Lab.reservation(key)`: one SQL read of `infrx.lab_run_reservations` |
| `lab.runs.reservations[held] == {"limit": ..., ...}` (i06) | same, plus the fake's key was `"limit"`, the real column is `amount` | `Lab.reservation(key)` returns `{"payer_ref","amount","state","cost"}`, the real columns |
| `(lab.NEMO, submit_key(...)) not in lab.runs.reservations` (i03, i02) | same `AttributeError` | `Lab.reservation(key) is None` |
| `lab.judge.spent == {...}` / `.committed(...)` (i03) | `AttributeError: 'PgTeacherLedger' object has no attribute 'spent'` | see below - not a 1:1 swap |
| `now=lab.judge.now` (i01's `labels1`, i05's `own_ledger`) | `AttributeError: 'PgTeacherLedger' object has no attribute 'now'` (the fake's `now` is a fixed value at construction; the real ledger's `db_now()` is an async method) | `now=run(lab.judge.db_now())` |

**i03's budget assertion needed more than a 1:1 substitution, found only by actually running it
against Postgres** (not guessable from the fakes' behaviour alone): `infrx.lab_run_reservations`
carries a foreign key to `infrx.lab_budgets`, and `lab_run_settle`/`lab_judge_settle` both credit
the *same* pooled `(provider_org_id, payer_ref)` row - P3's training runs and P2's teacher runs
share one PROVIDER_USD cap per payer in the real schema, by design. The fakes kept them in two
separate Python objects with independent state, which is not the real behaviour. Confirmed live:
after the fix, i03's original assertion (`budget.settled == str(total)`, reading the pooled
`infrx.lab_budgets` row) failed with `'12.53000000' == '0.03000000'` - `total` (the teacher's own
$0.03 across `labels1`+`iteration2`) plus i06's own $12.50 training-run settlement, both against
the same payer. Fixed by reading `infrx.lab_judge_runs.actual` for exactly `one.collected`'s
own `run_id`s (`Lab.judge_settled`), which isolates the teacher's contribution regardless of
what else has settled against the pooled budget by then, and keeping only `budget().reserved ==
"0.00000000"` as the pooled, order-independent "no hold left" check.

New `Lab` methods (`tests/integration/lab_improve/lab_world.py`): `reservation(key)`,
`budget()`, `judge_settled(run_ids)` - one SQL read each, over `infrx.lab_run_reservations`,
`infrx.lab_budgets`, `infrx.lab_judge_runs`.

## The new case: WR-P3-R184/R192 (check 1, again)

Added `test_i06_an_ambiguous_run_ends_only_on_an_operators_written_confirmation`
(`scenarios_faults.py`), registered in `runner.py`'s `REQUIRED["i06"]` (now two cases) and two
new stack mutants targeting `0042_lab_d8_ledgers.sql`'s `lab_external_run_move` directly
(`mutants.py`): `st_ambiguous_fails_without_an_operator` (removes the `is_operator` check) and
`st_confirmed_failure_keeps_the_hold` (disables the same-transaction hold release). The
pre-existing `a_required_case_renamed` layer-1 mutant's anchor text was updated for `REQUIRED`'s
now two-line `"i06"` tuple (unrelated content, same defect it always caught).

The case: drives a run to `ambiguous` (the existing timeout path), then proves in order -
`release()` refuses while ambiguous (`errors.StateConflict`); `move(target="failed")` without an
`operator` field refuses (`errors.Forbidden`); with an operator but a blank `confirmation_ref`
refuses (`errors.InvalidRequest`); the run stays `ambiguous` and the hold stays `held` through
both refusals; then the same move with both fields succeeds, the run reaches `failed`, and *the
same reservation row* is `released` - proving the SQL's same-statement `lab_run_release` call,
not a separate step.

## Checks

| # | command | exit | counts |
|---|---|---|---|
| - | `python -m py_compile` on every file in `lab_improve/` | 0 | clean |
| 2 | `uvx ruff check --line-length 100 tests/integration/lab_improve tests/integration/lab_{evaluate,observe,operate,rollout}/runner.py` | 0 | all checks passed |
| 2 | `cd apps/infrx-api && .venv/bin/python -m pytest -q tests/p` | 0 | 82 passed, 7 skipped (unaffected: no `infrx/pipelines/**` file changed) |
| - | `pytest tests/integration/lab_improve -q` (no stack, default mutant subset) | 1 | 17 passed, 1 failed (pre-existing `st_tombstones_ignored`-related, see WR-E7L-5's evidence), 1 skipped |
| - | `pytest tests/integration/lab_{evaluate,observe,operate,rollout} -q` | mixed | unaffected by this item; same two pre-existing baseline lines as WR-E7L-5's evidence |
| - | `make -n lab-improve` | 0 | resolves to `runner.py --out .../evidence/e/E7L-raw-a8e1d1f` unchanged in shape |

## The gate rerun (real services, e7l stack, at code head `a8e1d1f9`)

`apps/infrx-api/.venv/bin/python tests/integration/lab_improve/runner.py --keep --out
research/plan/evidence/e/E7L-raw-a8e1d1f/run` - **37.2 s**, `run/verdict.json`,
`run/scenarios.xml`, `run/runner-stdout.log` (raw evidence kept in this directory; `pins.dirty:
true` only because this raw directory was untracked during the run, as at every prior E7L
evidence head).

| cell | status | scenarios |
|---|---|---|
| DATA-LINEAGE | NOT RUN (was PASS on i01/i03/i04) | i01, i03, i04 PASS; i09 NOT RUN[staging-target] |
| PIPELINE-LINEAGE | NOT RUN | i01, i02, i03 PASS; i08 NOT RUN[LAB_PIPELINES,P3-evaluations] |
| PIPELINE-BUDGET | NOT RUN | i03, i05, **i06 PASS (both cases, incl. the new confirmation case)** |
| TRAIN-RECOVER | NOT RUN | i02, **i06 PASS (both cases)**; i07 NOT RUN[composition-2,WR-P2-4] |

**19 PASS / 0 FAIL / 6 NOT RUN** at the scenario-case level (`i01`-`i06`'s 6 scenarios, 19 named
cases, all PASS; `i07`-`i09` stay NOT RUN with their unchanged rerun commands - no regression
from the E7L-a609f36.md baseline's 17 PASS / 0 FAIL / 4 NOT RUN, and one case ahead of it (the
new WR-P3-R184/R192 case)). Every cell's NOT RUN is exactly the same unbound-lane reason as
before this item; nothing that used to PASS now fails.

Two prior runs at this same head (torn down, not kept as evidence) reproduced the identical
result before this one: the first (before the `--import-mode=prepend` fix below existed)
correctly surfaced the regression described next; the second, after that fix, and a third fix
for i03's budget assertion, matched this final run exactly.

## A WR-E7L-5 regression, found only by actually running the gate

The root `pytest.ini` WR-E7L-5 added (`addopts = --import-mode=importlib`) broke `runner.py`'s
own scenario subprocess (`pytest scenarios_faults.py scenarios_iterate.py`, run with `cwd=REPO`
against the live checkout - never a scratch copy, unlike the mutant-list's `_layer1`/`_stack`
copies, which never see the root ini and so never hit this): `ModuleNotFoundError: No module
named 'lab_world'`. These scenario files were correctly left untouched by WR-E7L-5 (never loaded
in-process alongside a sibling package - see that evidence's deviations section), but they still
rely on prepend mode's automatic sys.path insertion for their own bare `import lab_world`/
`observe_world`, which importlib mode does not do. Fixed by adding `"--import-mode=prepend"` to
all five packages' `runner.py`'s subprocess `argv` (overriding the root ini's addopts for that
one invocation only); verified this generalizes correctly since the flag and reasoning are
identical for all five, though only E7L's live gate was actually rerun end-to-end (E3L/E5L/E6L/
E8L belong to other lanes). Confirmed before/after: `ModuleNotFoundError` on the unpatched
runner.py, `18 passed, 4 skipped` (i03's budget assertion not yet fixed) then `18 passed, 4
skipped` again once i03's assertion was corrected too, matching this evidence's final run.

## The stack mutant list (kept stack, code head `a8e1d1f9`)

`INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e7l apps/infrx-api/.venv/bin/python -m pytest -q
-p no:cacheprovider -rA tests/integration/lab_improve/test_mutants.py` - exit 1, **45 passed / 2
failed** in ~385 s. Both failures are `st_tombstones_ignored` (`test_the_lists_are_well_formed`
and its own `test_stack_mutant_is_killed` case): datasets-lw5 moved the tombstone line the
mutant's anchor text targets in `infrx/datasets/lineage/__init__.py`; confirmed identical
(same name, same `misdeclared`/anchor-count detail) at the unmodified base commit `1d26fd59` in
a throwaway worktree, and already recorded as a pre-existing, out-of-scope baseline defect in
`WR-E7L-5-8609d0a2.md`. **0 survivors among every mutant this item is responsible for**: both new
R184/R192 mutants (`st_ambiguous_fails_without_an_operator`, `st_confirmed_failure_keeps_the_
hold`) pass, and every other one of the 25 STACK + 18 LAYER1 + 2 self-tests (45 total, unchanged
by this item) also passes. `FAILING` in `mutants.py` stays `()`: `st_tombstones_ignored` is not
this item's decision to relabel.

**Teardown:** `runner.py --reuse --only i09 --out /tmp/.../teardown` removed
`infrx-e7l-clickhouse/postgres/s3/valkey`; `docker ps -a`/`docker volume ls` confirm no `infrx-e7l-*`
container, network or volume remains.

## Label

Synthetic fixtures and serving; no real-model improvement claim (P-07), no P-10/P-11 live
evidence, no GPU or hosted claim. D8/D6J are now the *real* ledgers on this session's own
PostgreSQL (WR-E7L-4 closes that stand-in); the remaining stand-ins are exactly what the verdict
already named: P3's `Evaluations` port (B3's, WR-E7L-1) and N3's content port (`FakeContent`
over the real L2 directory and T3's real in-memory `Retention`, C2's adapter not composed:
WR-N3-1).

## Wiring requests

None. Every touched path is inside `tests/integration/lab_improve/**` (owned) or the other four
packages' `runner.py` (a narrow, identical, mechanical fix to a regression this branch itself
introduced - not a change to their scenario logic or product code).

## Rulings

None proposed. This rebinds a test world to already-merged, already-ruled adapters (D8, R184
numbered elsewhere) and closes a stand-in the verdict already disclosed; no new product decision.

## Estimate (remaining on E7L: none - this closes the brief's three items)

optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium.

Basis: this item (tracing the D8 schema, the rebind, three fail-first fixes including the
cross-regime budget pooling found only by running it live, the new R184/R192 case and its two
stack mutants, the WR-E7L-5 regression fix across five packages, and two full gate + mutant
verification passes) took about 2 h including the 385 s mutant run and two ~40 s gate runs. What
would remain for an *accepted* E7L is unchanged from WR-E7L-5's estimate: i07 after
composition-2 + a P2/P3 worker pass (WR-P2-4), i08 after WR-E7L-1 + LAB_PIPELINES, i09 on an
allocated staging target, and one coordinator verify round (47-234 min, session-03 basis) - none
of which this lane's brief asked for.

## Audit log

- 2026-09-29: created at code head `a8e1d1f9` (WR-E7L-4: D8's real PgLabelLog/PgRunLedger/
  PgTeacherLedger composed into `lab_world.py`; three scenario assertions rebound to D8's real
  tables; the new WR-P3-R184/R192 case and its two stack mutants; a WR-E7L-5 regression across
  all five packages' `runner.py` found and fixed; gate rerun 19 PASS / 0 FAIL / 6 NOT RUN, ahead
  of the E7L-a609f36.md baseline by one case; stack mutants 45 passed / 2 failed, both the
  pre-existing `st_tombstones_ignored` defect, 0 survivors among this item's own mutants).
