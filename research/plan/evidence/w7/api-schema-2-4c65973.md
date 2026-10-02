# api-schema-2 (AP-00 00d remainder): 0065, 0066, the pins, the whole-set re-proof — evidence at 4c65973

Lane api-schema-2, wave 7 batch 2 (LW7), branch `codex/w7-api-schema-2`, worktree
`.claude/worktrees/codex-w7-api-schema-2`, base `b05eb6f4`, code head `4c659731`. Task AP-00.
Task-local key `ap0` only (PostgreSQL 57550; containers `infrx-ap0-postgres`,
`infrx-ap0-postgres-supabase`). Nothing hosted, no box, no AWS/SSM/S3, no Vercel, no secret;
`infra/rollout/hosted-migrate.sh` untouched (R269); 0001–0059 untouched.

## Changed paths (all owned)

| Path | What |
|---|---|
| `apps/app/supabase/migrations/0065_identity_functions.sql` | new, LOCAL-ONLY: SR-AP01-1's six SECURITY DEFINER doors `infrx.identity_{account,user_by_email,members,grant_member,revoke_member,create_provider}`; EXECUTE service_role + infrx_lab_control only; `-- rollback:` line |
| `apps/app/supabase/migrations/0066_wave7_grants_and_reads.sql` | new, LOCAL-ONLY: SR-AP10-1 (`lab_eval_catalog`; `lab_experiments` + run refs), SR-AP10-2 (`lab_external_runs_of`, `lab_checkpoint_receipts_of`), SR-AP10-3 (control-login EXECUTE on nine route reads), AP-07's revoke-only door `lab_withdraw_access_grant`, `control_op_cancel` re-created (a finished operation answers as is); four `-- rollback:` lines |
| `apps/app/supabase/migrations/README.md` | allocation rows 0065, 0066; the re-proof sentence |
| `apps/infrx-api/infrx/state/control_ops.py` | `FakeControlOps.cancel` + the protocol docstring: the 0066 cancel rule (no new method) |
| `apps/infrx-api/tests/d/test_upgrade_0065_mutants.py` | new: 4 checks over AP-01's identity world (`tests/ap01/worlds.seed_pg`), 15 SQL mutants, 3 guards |
| `apps/infrx-api/tests/d/test_upgrade_0066_mutants.py` | new: 5 checks over L3-SQL's world, 21 SQL mutants, 3 guards |
| `apps/infrx-api/tests/d/test_control_ops_units.py` | the cancel scenario: a finished operation answers a cancel as it is |
| `apps/infrx-api/tests/d/test_control_ops_mutants.py` | the five cancel mutants re-anchored in 0066 (`_s66`); `cto_py_cancel_finished` dropped (its anchor is gone; `cto_py_cancel_reopens` kills the same defect) |
| `apps/infrx-api/tests/d/test_control_ops_upgrade.py` | the R151 rehearsal widened to the whole wave-7 set (below) |
| `apps/infrx-api/tests/d/test_upgrade_lab.py` | register row 88 (below) |
| `apps/infrx-api/tests/i/test_known_good_proof.py`, `tests/i/lab/test_lab_rollout_steps.py`, `tests/integration/test_harness.py` | the admitted LOCAL-ONLY range 0060..0066 (the harness list names 0065, 0066) |
| `research/plan/evidence/w7/api-schema-2-4c65973-WR-AS2.patch` | the wiring requests' exact patch |

## Register row 88: root cause (no grant was missing)

`test_upgrade_lab.py::test_the_lw4_reads_keep_the_listings_already_written` impersonated the
control login with `set local session authorization infrx_lab_control`. On the Supabase image
the harness login `postgres` is not a superuser (measured: `rolsuper=false`, `supabase_admin`
is), so `SET SESSION AUTHORIZATION` is refused with 42501 *before and after* 0044. The case's
"before" assertion (`== "42501"`) therefore passed vacuously there and its "after" failed.
`set local role` fails the same way: PostgreSQL 16+ gives `postgres` only ADMIN on 0043's role
(`pg_auth_members`: `admin_option=t, set_option=f`). With the session able to become the role
(transactional self-grant `grant infrx_lab_control to current_user with inherit false, set
true`, rolled back with the read), the 0044 grant + policy are present and correct on both
images: catalog_listings ACL `infrx_lab_control=r`, policy `lab_control_reads_listings`. Fix
in the test (owned), not in 0066; 0044 untouched. Red/green: Supabase image at the base
1 failed (`'42501' == [...]`); after the fix passes on both images.
Same pattern, other owners' files (not changed; on the Supabase image each either passes
vacuously or fails 42501 — seven such checks fail there at the base, see the commands table):
`tests/d/test_d8_requests.py:662,715`, `tests/d/test_l3sql_reads.py:103,110,264`,
`tests/r/routing/test_routing_pg.py:43` (session authorization), and every `set local role
infrx_lab_control` refusal probe (e.g. lw8's worker-claim check). Proposed: one shared
`pgharness.as_role(conn, role)` doing this self-grant, adopted by those files.

## 0065 (SR-AP01-1)

The DDL is the request's, plus `#variable_conflict use_column` in the three PL/pgSQL bodies:
as given, `identity_grant_member`/`identity_revoke_member`/`identity_create_provider` fail
42702 (their RETURNS TABLE names `user_id`, `revoked_at`, `slug`… are also the columns in the
ON CONFLICT targets/WHERE). Measured on the first green attempt; recorded in the header.
Oracle: on the control login every door answers exactly what `PgIdentity`'s platform SQL
(`infrx.console.session.ACCOUNT`/`MEMBERS`) answers for each of AP-01's ten people + an unknown
id; an email two profiles share and an unknown email resolve to nobody; grant once/retry
(`created` false)/other role `state_conflict`/revoke answers twice/never-member `not_found`;
a taken slug is refused and keeps its name; EXECUTE = service_role + control login only.
Not granted to `infrx_runtime` (its set is pinned by `checks_reads.RUNTIME_FUNCTIONS`); the
request's "(and infrx_runtime if the gateway ever logs in as it)" is a coordinator decision
(the production gateway does log in as it, step 55) — see schema requests.

## 0066

| Item | Decision |
|---|---|
| SR-AP10-1 (a) `lab_eval_catalog` | `{datasets [{ref, label: "<id> v<version>"}], harnesses [{ref, harness_id, version, adapter}], evaluators [{ref, label: metric}], servings [{ref: lab_serving_ref, label: revision_label}]}`; servings = the provider's `environment='dev' and visibility='private' and state='ready_private'` revisions (a private PROD revision, a draft, a public proposal are excluded — each proven) |
| SR-AP10-1 (b) | `lab_experiments` re-created with `baseline_run_ref`/`candidate_run_ref` (only change); d8's two mutants anchored in 0043's body move to 0066's list (`ap0066_experiments_unscoped`, `ap0066_experiment_any_report`) — WR-AS2-2 removes them from `code_mutants_d8.py` |
| SR-AP10-2 | `lab_external_runs_of` → `[{external_run_id, doc}]`; `lab_checkpoint_receipts_of` → `[{checkpoint_id, external_run_ref, artifact_digest, state, reason}]` with the provider's own `checkpoint:<id>` note over the receipt (exactly `tests/g/lab_pipelines`' fake); oldest first |
| SR-AP10-3 | control login EXECUTE on `lab_put_experiment, lab_checkpoint_listing, lab_list_datasets, lab_evaluator, lab_checkpoint_subscribe, lab_checkpoint_decisions` + the three new reads. R251 pin moves (WR-AS2-2) |
| AP-07 suspended org | a new revoke-only door `infrx.lab_withdraw_access_grant` (0027's revoke without `lab_grant_version`'s suspension refusal; owner check + pair lock kept); 0027's put/revoke unchanged (a suspended org still grants/re-scopes nothing); platform role only (the console data-use pool). Not a redefinition of `lab_grant_version` (that would supersede L2-SQL's anchors) |
| api-artifacts' 0060 request | `cancel` of a succeeded/failed operation now answers it as it is (0060 refused with 409); queued→cancelled, running→cancel_requested unchanged. `get` already returns `actor` (audience + ids) and `resource_kind/resource_id`; the artifacts worker has no login of its own (WR-LDP-7 not done) and runs on `infrx_lab_control`, which 0060 already grants all seven doors — nothing to grant |
| AP-03's conditional `infrx_runtime` grants | NOT written: the decision recorded in `api-actions-75b8d25.md` ("none for the default composition") and WR-AP03-2's merged settings comment (the actions pool must `set role`; never `infrx_runtime`) |

## The whole-set re-proof (R271) and the R151 rehearsal

`test_control_ops_upgrade.py` now: 0001–0026 + consumer history (6 seeded job states) → 0027–0059
→ history table at 0059 → `deploy/migrate.py plan` lists exactly the wave-7 files
(`0060 0061 0064 0065 0066`) → `apply --expect <digest>` exit 0 → +9 tables (empty); 96 existing tables' counts,
money sums, jobs, every relation/column ACL unchanged; existing function grants moved = exactly
0066's six regrants + 0064's three (`lab_judge_calibration`, `lab_judge_request_run`,
`lab_review_feedback`, each `+infrx_lab_control=X`); new tables empty → re-run is a no-op →
0066's then 0065's `-- rollback:` lines → their 10 functions gone, the six grants revoked,
`lab_experiments` and `control_op_cancel` back to 0043's/0060's bodies (whitespace-normalized)
→ forward again equals the first apply → an operation starts. 0061/0064 carry prose rollbacks
(their lanes' upgrade proofs stand); a rollback of the whole set below 0065 is not rehearsed.

## Commands (exit codes, counts) — from `apps/infrx-api` unless noted

| Command | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=ap0 … pytest -q tests/d/test_control_ops_upgrade.py tests/d/test_upgrade_lab.py tests/i/…` at the base | 1 | rehearsal fails at the base (`0060 is the only file past 0059`: 0061/0064 present) — pre-existing |
| `INFRX_D1_IMAGE=supabase INFRX_D_TASK=ap0 … test_upgrade_lab.py -k lw4_reads` at the base | 1 | row 88 reproduced: `'42501' == [...]`; diagnosis above (scratch probe) |
| `… pytest -q tests/d/test_upgrade_0065_0066_mutants.py -k checks` before 0065 (RED; the file was split into `_0065_`/`_0066_mutants.py` after) | 1 | 4 failed: UndefinedFunction |
| same after 0065 as requested | 1 | 2 failed: 42702 ambiguous `user_id`/`slug` → `#variable_conflict use_column` → 7 passed |
| `… test_upgrade_0066_mutants.py -k checks` before 0066 (RED) | 1 | 5 failed (42883 / 42501 / missing refs) |
| pins before widening, with 0065 present (RED) | 1 | `test_ops_recover__hosted_migrate_expects…`, `test_ldp__todays_hosted_migrate…` failed; harness `test_the_migration_set…` failed |
| `pytest -q tests/d/test_control_ops_units.py -k cancel` after the scenario change (RED) | 1 | 1 failed (the fake still refused) → green after `FakeControlOps.cancel` |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap0 … pytest -q tests/d/test_upgrade_0065_mutants.py tests/d/test_upgrade_0066_mutants.py` | 1→0 | first: 0065 2 equivalent mutants dropped (`kind='consumer'`: a provider_dev wallet has no owner_user_id; `revoke from public`: 0004's default privileges), 1 survivor fixed (seeded a revoked member); 0066 3 survivors fixed (private prod revision, a foreign provider's note, `.get` refs) → rerun of the three: 3 killed. Final: 0065 15/15, 0066 21/21 killed; guards green |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap0 … pytest -q tests/d/test_control_ops_mutants.py` | 0 | 104 passed: 59 SQL (5 now anchored in 0066) + 42 Python killed, 3 guards |
| `INFRX_D_TASK=ap0 … pytest -q <every non-mutant tests/d file> tests/l3sql tests/i/test_migrate.py` (plain) | 1 | 791 passed, 1 skipped, 8 xfailed, 2 failed: `test_d8_requests::check_the_control_login_is_bounded_and_lab_only` (probes `lab_list_datasets` as a Lab RPC the login must not reach — SR-AP10-3 grants it; WR-AS2-2) and `test_operations_pg::test_api_ops__the_operations_service_runs_on_the_postgres_adapters` (`replayed True != False`; fails identically with 0065/0066 removed — pre-existing, not this lane) |
| every `tests/d/test_*mutants*.py` guard (`well_formed`, `superseded`, `every_case`) | 1 | only `test_code_mutants_d8::test_no_mutant_anchors_in_a_superseded_function_body` (expected; WR-AS2-2). 4 other failures in that run were harness contention with a concurrent ap0 run; rerun serially: 4 passed |
| `INFRX_MUTANTS=all INFRX_D1_IMAGE=supabase INFRX_D_TASK=ap0 … pytest -q test_control_ops{,_units,_upgrade}.py test_upgrade_lab.py test_upgrade_0065_mutants.py test_upgrade_0066_mutants.py test_control_ops_mutants.py tests/i/test_migrate.py --deselect …::test_code_mutant_is_killed` | 0 | 149 passed on `supabase/postgres@sha256:7768d0d1…` (PostgreSQL 17.6): the rehearsal, row 88, 0065 4+15, 0066 5+21, control_ops checks + 59 SQL mutants |
| `INFRX_D1_IMAGE=supabase INFRX_D_TASK=ap0 … <every non-mutant tests/d file> tests/l3sql tests/i/test_migrate.py` | 1 | 785 passed, 1 skipped, 8 xfailed, 8 failed — the same 8 fail with 0065/0066 removed (rerun): `test_operations_pg` (as on plain) and seven control/runtime-login checks refused 42501 on the role switch (`permission denied to set session authorization`/`set role`: the row-88 class) in `test_d8_requests` (4), `test_l3sql_control` (1), `test_l3sql_reads` (2) — pre-existing on this image, not this lane |
| `INFRX_MUTANTS=all … tests/i/test_mutants.py -k "hosted_migrate_pending_short or …anchor_stale or …post_check_stale or well_formed or every_case"` | 0 | 5 passed (the three pin mutants killed) |
| `INFRX_MUTANTS=all … tests/i/lab/test_mutants.py -k "r151_post_check_file_name or well_formed or every_case"` | 0 | 3 passed |
| `pytest -q tests/i/test_known_good_proof.py tests/i/lab/test_lab_rollout_steps.py` | 0 | 35 passed |
| `tests/integration/test_harness.py` (repo root) | 1 | 47 passed, 1 failed: `test_nothing_in_this_directory_points_at_production` (`api-lifecycle.sh` names `AWS_SECRET_ACCESS_KEY`) — identical at the base, not this lane |
| WR-AS2-1+2 applied transiently: `tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed (7 failed without WR-AS2-1) |
| WR-AS2-2 applied transiently: `INFRX_MUTANTS=all INFRX_D_TASK=ap0 … test_code_mutants_lw8.py test_code_mutants_lr7.py` | 0 | 19 passed |
| WR-AS2-2 applied transiently: `… test_code_mutants_d8.py -k "q_role or q_experiment or well_formed or superseded or every"` | 0 | 19 passed (incl. `q_role_other_lab` re-pointed, superseded guard green) |
| WR-AS2-2 applied transiently: `… test_d8_requests.py` | 0 | all checks passed |
| `make api-lint` (repo root) | 0 | All checks passed |
| `make api-typecheck` (repo root) | 0 | `pyright: 458 errors (baseline 458)` — no new error |
| `python3 research/plan/scripts/validate_plan.py` (repo root, with this file) | 0 | PASS (915 local Markdown links across 482 documents) |

## Wiring requests (exact patch: `api-schema-2-4c65973-WR-AS2.patch`)

- **WR-AS2-1 (`Makefile` api-mutants)**, after the 0060 line:
  `cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_upgrade_0065_mutants.py tests/d/test_upgrade_0066_mutants.py`
  (+ comment). Composed test: `tests/integration/test_makefile_mutant_lists.py` 7 passed.
- **WR-AS2-2 (R251 pin + the two lists 0066 supersedes)**: `tests/d/test_code_mutants_lw8.py`
  GRANTED + the nine SR-AP10-3 names; `tests/d/code_mutants_d8.py` drops
  `q_experiment_any_report`/`q_experiments_unscoped` (now 0066's `ap0066_*`) and re-points
  `q_role_other_lab` at `lab_tombstone_samples` (0041; a worker-only door the login must not
  reach); `tests/d/test_d8_requests.py`'s `other_lab` probe likewise. Composed tests: the three
  rows above (19 + 19 passed, d8 checks green).

## Schema requests

None outstanding. Conditional (coordinator decision): if IDENTITY_API / CONSOLE_ACTIONS_API /
CONSOLE_DATA_USE are composed on the gateway's production login `infrx_runtime` (step 55),
it needs EXECUTE on 0065's six doors and `lab_withdraw_access_grant` and AP-03's set — a new
file plus a move of `checks_reads.RUNTIME_FUNCTIONS` (0060's precedent).

## Follow-ups for the consuming lanes (no change here)

api-identity: `PgIdentity` → `infrx.identity_*` (one line per method), then mount `lab_workspaces`
on the Lab unit. api-improve: `Catalog.catalog` → `infrx.lab_eval_catalog`; `Experiments` read
`baseline_run_ref`/`candidate_run_ref`; `lab.compose.RunLedger.run_rows/checkpoint_rows` →
`lab_external_runs_of`/`lab_checkpoint_receipts_of` (register row 15). api-traces-2:
`DataUse` revocation → `lab_withdraw_access_grant`. api-artifacts-2/api-hosting: `cancel` of a
finished operation returns it (no 409).

## Proposed rulings (unnumbered; next free R272)

- A control operation's cancel is idempotent and race-free: queued → cancelled, running →
  cancel_requested, any other state answers the operation as it is (never 409).
- A suspended organization's owner can withdraw a current sharing grant (the revoke-only door)
  but can never grant or re-scope one; withdrawal is never refused for suspension.
- Test harness: a check that runs as a login role takes SET on it in its own rolled-back
  transaction; `SET SESSION AUTHORIZATION` is not used (the Supabase image's `postgres` is no
  superuser).

## Open items

- The Supabase-image vacuity of the other `session authorization` / `set role` refusal probes
  (row-88 class, files listed above) — owners' follow-up.
- `test_operations_pg::test_api_ops__…postgres_adapters` fails on ap0 at the base too.
- The hosted R151 window for 0060–0066 (reviewed `hosted-migrate.sh` edit + re-proof) is the
  coordinator's (R269).

## Estimate (remaining for AP-00 00d)

optimistic 0.5 h / likely 1.5 h / pessimistic 4 h; confidence medium. Basis: 0065/0066, the
pins and the re-proof are done at 4c65973 (~6 h spent against 5/8/14); left are WR-AS2-1/2 at
merge, one review round, and a re-run of the rehearsal if api-hosting's 0062 merges first (the
rehearsal admits it; ~5 min per image).

## Log

- 2026-10-02: written at code head 4c65973 by the api-schema-2 lane.
