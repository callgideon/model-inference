# LAB-SQL-LW8: SR-LCR-1 (LCR-F1 closed): 0056 grants the Lab control login its families' route halves (`6f28a64`)

Lane lab-sql-lw8 (wave LW6, task L3 / lab-sql), branch `codex/w5-lab-sql-lw8`, base `3f2ff579`,
implementation head `6f28a64c` (evidence commit on top). Opus implementer. Nothing hosted, no box,
no AWS/SSM/S3/Vercel, no secrets; task-local docker on key l4 (57503) only.

## Base note (deviation)

`tests/i/lab_control/` (lab-control-routes, a01c0a91) is not on `3f2ff579`; merge #51 integrates it
in parallel. Commit `(0)` merges `codex/w5-lab-control-routes` at a01c0a91 (a plain `--no-ff` merge,
the same commit #51 integrates, so git sees it as common history) so the PG matrix this lane pins
exists; this branch's own diff is only the owned paths below.

## What changed

| Path | Change |
|---|---|
| `apps/app/supabase/migrations/0056_lab_control_grants.sql` (new, LOCAL-ONLY) | one `grant execute ... to infrx_lab_control` on 30 functions; nothing revoked, no other role touched |
| `apps/infrx-api/tests/d/test_code_mutants_lw8.py` (new) | 3 checks (run pristine as `test_lw8_grants`), 7 SQL mutants over 0056 (D7's `kill` runner), list/coverage/superseded guards |
| `apps/infrx-api/tests/i/lab_control/test_control_routes_pg.py` | matrix expectations only: `EXPECTED` pinned per family, both logins equal to it; a worker claim refused (42501) on the Lab login |
| `tests/integration/test_harness.py` | the migration pin: `0056_lab_control_grants.sql` after 0053 |
| `Makefile` | api-mutants: `cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_code_mutants_lw8.py` (the c6 shape) |

## The grant list: derived from the code; where it differs from the evidence list

Derived by tracing `infrx.lab.control.app._families` → `pilot._lab` / `_lab_2` / `lab_releases` /
`_lab_checkpoints` / `_teachers` → the routes → their services (`datasets.{imports,lineage,versions}`,
`pipelines.{annotations,training}`, `evaluation.{runner,checkpoints}`, `ReleaseRecords` /
`ReleaseProposals`) → the `Pg*` store method → the SQL function. Only calls a route handler can
reach on the unit count; worker halves (`imports.work`, `checkpoints.on_checkpoint`, runner leases,
the outbox pass, teacher/judge submission) do not.

| family | functions (30) |
|---|---|
| datasets (D7, 0051) | `lab_resolve`, `lab_publish`, `lab_accessible_samples`, `lab_import_job_enqueue`, `lab_import_job` |
| evaluations (D7) | `lab_run_status`, `lab_cancel_run` |
| pipelines (D7/D8/0053) | `lab_label_append`, `lab_label_events`, `lab_external_run_get`, `lab_external_run_move`, `lab_run_reserve`, `lab_run_release`, `lab_run_settle`, `lab_pipeline_note`, `lab_pipeline_noted`, `lab_receive_checkpoint`, `lab_checkpoint_transition`, `lab_checkpoint_receipt`, `lab_checkpoint_subscriptions`, `lab_put_evaluator`, `lab_create_run` (B1's freeze under p3.approve's evaluation) |
| teacher batches (D8/J2) | `lab_judge_run` (`PgTeacherLedger.run`, a read), `lab_teacher_failures` |
| releases (D9/0043/0053) | `lab_releases_in`, `lab_release`, `lab_release_decisions`, `lab_release_proposals`, `lab_propose_release` |
| checkpoints receiver (D8) | `lab_checkpoint_record_event` (+ D7's receive/resolve above) |

The login now executes 43 `infrx.lab_*` functions (13 from 0043/0052 + 30).

**Differs from LAB-CONTROL-ROUTES-79537d3's SR-LCR-1 list:**
- In the code, not in that list: `lab_pipeline_note`, `lab_pipeline_noted` (p3 import/approve and
  the checkpoint page), `lab_checkpoint_record_event` (the receiver), `lab_checkpoint_receipt` and
  `lab_release_decisions` (0053, named there only as "composition-6's 0053 reads"), `lab_judge_run`
  (the teacher batch read).
- In that list, not called by any route on the unit (not granted): `lab_list_datasets`,
  `lab_dataset_uses` (LabAccess has no `datasets` port on the unit), `lab_register_source` (only
  `imports.work`, the I5 pool), `lab_run_results`, `lab_eval_report`, `lab_variant_comparisons`
  (no caller outside the store), `lab_experiments`, `lab_put_experiment`, `lab_evaluator`,
  `lab_checkpoint_listing`, `lab_checkpoint_subscribe` (evaluations' `experiments` / `ledger` /
  `catalog` ports are not composed on the unit: WR-B4-2, the route answers 503 on both logins),
  `lab_checkpoint_event(s)`, `lab_checkpoint_decide`, `lab_checkpoint_reject`,
  `lab_checkpoint_decisions` (the bridge `on_checkpoint`, a worker), `lab_decide_release_proposal`
  (operator CLI only, R240), `lab_teacher_reserve/_record_sent/_record_failures` (teacher worker).
  `tests/d/test_d8_requests.py`'s bounded-login check still requires `lab_list_datasets` to be
  refused to this login (42501): granting the evidence list would have broken it.
- When WR-B4-2 composes the evaluations ports on the unit, their functions need the next grant.

## Pre-grant vs post-grant matrix (PG case, l4, A's administrator, probe `ds@1` absent)

| family | lab before 0056 | lab after = owner (pinned `EXPECTED`) |
|---|---|---|
| control `/models` | 200 | 200 `{"data":[{"model_id":"nemostation/marlin-2b"…` |
| datasets `versions/ds@1` | 503 `the datasets service failed` (InsufficientPrivilege) | 404 `not_found: no such Lab record for this provider` |
| evaluations `/runs` | 503 unavailable | 503 unavailable (experiments port unwired, WR-B4-2) |
| pipelines `/disagreements` | 503 unavailable | 404 `{"refusal":"not_found"}` |
| teacher-batches | 503 | 503 (LAB_TEACHERS off, P-10) |
| releases | 503 unavailable | 200 `{"data":{"releases":[],"decisions":[],"proposals":[]}}` |
| optimizations | 503 | 503 (WR-C6-VARIANTS; lab-sql-lw7's 0055 changes it) |

## Commands (exit, counts) — from `apps/infrx-api` unless noted

Fail-first (step 1, `4111e230`, 0056 absent):
- `INFRX_D_TASK=l4 uv run --frozen pytest -q -m pg tests/i/lab_control` → exit 1, 1 failed: lab
  datasets/pipelines/releases 503 vs owner 404/404/200 (owner column equal to `EXPECTED`).
- `INFRX_D_TASK=l4 … pytest -q tests/d/test_code_mutants_lw8.py -k test_lw8_grants` → exit 1, 2 failed
  (30 route halves missing; EXECUTE held by service_role only), 1 passed (worker claim refused).
- `… -k "well_formed or every_case or superseded"` → exit 1, 2 failed (0056 absent), 1 passed.

After step 2 (`6f28a64c`):

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `INFRX_MUTANTS=all INFRX_D_TASK=l4 uv run --frozen pytest -q -s tests/d/test_code_mutants_lw8.py` | 0 | 13 passed: 3 checks, 3 list guards, **7 mutants killed, 0 survivors** (below) |
| 2 | root: `apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_harness.py -k migration_set` | 0 | 1 passed |
| 3 | `INFRX_D_TASK=l4 uv run --frozen pytest -q -s -m pg tests/i/lab_control` | 0 | 1 passed; lab column = owner column = `EXPECTED` |
| 4 | `uv run --frozen pytest -q tests/i/lab_control` | 0 | 13 passed, 1 skipped (PG case without a key) |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab_control/test_mutants.py` | 0 | 22 passed (19 mutants killed, 0 survivors) |
| 6 | `INFRX_MUTANTS=all INFRX_LAB_API_PG=1 INFRX_D_TASK=l4 … tests/i/lab_control/test_mutants.py` | 0 | 22 passed, 0 survivors (a clean run; two earlier runs had 3 then 2 `broken_runner` - the PG case errored in a mutant copy while lab-rollout-5 held the l4 lock, see Open issues) |
| 7 | `INFRX_D_TASK=l4 … pytest -q tests/d/test_d8_requests.py tests/d/test_l3sql_reads.py tests/d/test_l3sql_control.py tests/d/test_upgrade_lab.py tests/d/test_l2sql_access.py tests/d/test_upgrade_split.py tests/d/test_c6_reads.py` | 0 | 52 passed (every suite that pins `infrx_lab_control` or the Lab upgrade) |
| 8 | `uv run --frozen ruff check tests/d/test_code_mutants_lw8.py tests/i/lab_control/test_control_routes_pg.py` | 0 | clean |
| 9 | root: `INFRX_D_TASK=l4 make api-test` | 2 | 6771 passed, **7 failed**, 176 skipped, 10 xfailed (1:26:06): all 7 = `tests/d/test_outbox_relay.py[valkey]` `HarnessBusy: another run holds /tmp/infrx-d2-valkey-55463.lock` (the Valkey harness's shared d2 lock, held by another run; nothing touched) |
| 10 | `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_outbox_relay.py` (rerun, lock free) | 0 | 20 passed - so `make api-test` has 0 failures attributable to this lane; it includes the E4 regression (tests/g tests/w tests/contracts tests/i/test_packaging.py) |

Mutants (#1): `lw8_drop_resolve` / `lw8_drop_noted` / `lw8_drop_propose` → HOLDS "missing
['lab_resolve'|'lab_pipeline_noted'|'lab_propose_release']"; `lw8_grant_worker_claim` → WORKER
"infrx_lab_control reached infrx.lab_import_job_claim"; `lw8_grant_to_public` /
`lw8_grant_to_authenticated` / `lw8_grant_to_runtime` → BROWSER "EXECUTE held by other roles".
Every check is named by a mutant (`test_every_case_is_covered_by_a_mutant`). The PG matrix case
keeps its name, so `tests/i/lab_control/mutants.py`' coverage is unchanged.

**R150 note:** the brief's "revoke defaults from public/anon/authenticated/service_role" would
revoke `service_role`'s EXECUTE (0004's default privileges), which the App gateway and the Lab
workers use on `set role service_role`; 0056 only grants. The check pins each granted function's
holders to exactly {service_role, infrx_lab_control}: anon, authenticated, PUBLIC and infrx_runtime
never gain it (R237).

## Wiring requests / proposed texts

**WR-LW8-1 (runbook `research/plan/consumer-v1/08-lab-internal-testing-rollout.md`, as on
codex/w5-merge-51; proposed, not edited):**
- §0 row `/datasets, /annotations, …` "Internal testing today": `**no**: datasets/annotations/
  training/evaluations/releases answer "unavailable" on the box until SR-LCR-1 (lane lab-sql-lw8)
  grants …` → `**yes on the unit** (SR-LCR-1, 0056, lab-sql-lw8: infrx_lab_control executes the
  families' 30 route halves; datasets/pipelines/releases answer as on the owner login, LCR-F1
  closed); evals/pipelines/releases ports still typed-unavailable until WR-B4-2 / WR-LAB2-4 /
  WR-R4-1/2 / WR-P4B-1; never a 500 (LDP-F3 fixed). Proof: tests/i/lab_control PG matrix (l4) +
  `make lab-local` o05's control-factory case`. 0056 is LOCAL-ONLY: on the box the grant exists
  only after a hosted apply under R151/R201's three conditions, and R236 applies per family.
- §8 open items, the SR-LCR-1 bullet → `- **SR-LCR-1** done (lab-sql-lw8, 0056 local-only);
  rerun `make lab-local` (o05's control-factory case).`
- L5 row: `NOT RUN[SR-LCR-1] until lab-sql-lw8` → `expected PASS for datasets/pipelines/releases
  after lab-sql-lw8 (0056); evaluations/teachers/optimizations as their ports`.
- Closing paragraph: `families typed-unavailable on the box until SR-LCR-1 (lab-sql-lw8, LCR-F1)`
  → `families served on the unit's login after SR-LCR-1 (0056, local-only until a hosted apply)`.

**WR-LW8-2 (`make lab-local` o05 control-factory rerun, after merge #51 + this lane + WR-LCR-5):**
`make lab-local` (≈45 min; needs lab-on + e3l free). Expected: o05's control-factory case judges
datasets/pipelines/releases like the owner (no NOT RUN[SR-LCR-1]); WR-LCR-5's `NOT RUN[SR-LCR-1]`
marker is dropped for those families.

**WR-LW8-3 (lab-sql-lw7 / next lab-sql lane):** lw7's 0055 adds the R3 variants listing behind
`/lab/v1/optimizations` and `POST imports/{id}/requeue` - both route halves on the unit. Its
functions need `grant execute ... to infrx_lab_control` (in 0055 itself, or a follow-up) and
`BEFORE`/`GRANTED` in `tests/d/test_code_mutants_lw8.py` then gain them, else
`check_the_control_login_holds_exactly_the_route_halves` names them as extra (or the families
answer 503 on the unit). Whichever merges second updates the set.

## Proposed ruling (unnumbered)

"The Lab control login (`infrx_lab_control`) executes exactly the functions the control unit's
route halves call - 0043/0052's and 0056's - never a worker's claim, lease or submission; each
later Lab migration that gives a route a new function grants it to this login in the same file,
and `tests/d/test_code_mutants_lw8.py` pins the set."

## Open issues

- l4 contention: lab-sql-lw7 ran its E4 regression on l4 (`INFRX_D_TASK=l4`, ≈20 min) and
  lab-rollout-5 and a merge-51 clone used l4 during this lane; the harness lock serialised them
  (HarnessBusy, no container touched), and two PG mutant runs had broken_runner copies from it.
- WR-LW8-3 (0055's functions), WR-B4-2's later ports: the grant set grows with them.
- The matrix probes one read per family; the write halves (import enqueue, labels, proposals,
  p3 moves) are covered by the grant check, not by a route call on the Lab login.

## Estimate (remaining for SR-LCR-1)

optimistic 0.3 h / likely 0.75 h / pessimistic 2 h, confidence medium. Basis: lane work done
(0 survivors); remaining = coordinator merge (conflict on the PG matrix with #51: take this
side) + WR-LW8-3 reconciliation with 0055 (≈15 min) + one `make lab-local` rerun (≈45 min).
