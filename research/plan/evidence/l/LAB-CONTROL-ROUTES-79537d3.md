# LAB-CONTROL-ROUTES: WR-LDP-2 + LDP-F1 (b) + LDP-F3 (+ LDP-F7) on the Lab control unit (`79537d3`)

Lane lab-control-routes (wave LW6, task I2L), branch `codex/w5-lab-control-routes`, base
`046f4322`, implementation head `79537d3e` (evidence commit on top). Opus implementer. Nothing
hosted, no box, no AWS/SSM/S3/Vercel, no secrets; task-local docker on keys l4 (57503), dlab
(57500) and, for one scratch `make lab-local`, the lab_local composition's own e3l stack.

## What changed

| Path | Change |
|---|---|
| `apps/infrx-api/infrx/lab/control/app.py` | `_families()`: `pilot._lab` + `pilot._lab_checkpoints` on the unit's own login and session verifier with every family on (datasets, evals, pipelines incl. teacher batches, releases/optimizations; checkpoints when `LAB_CHECKPOINT_KEYS` is set); `create_app` registers `lab_datasets`, `lab_evaluations`, `lab_pipelines`, `lab_releases`, `lab_checkpoints` beside control/traces; `connector(..., set_role=False)` in `_store()` and `_compose()` (LDP-F7); the Lab objects are `lab_workers.lab_objects` on `LAB_S3_BUCKET`, else `NoObjects` (every use a typed 503) |
| `apps/infrx-api/infrx/gateway/routes/lab_datasets.py` | LDP-F3 only: `guarded` renders any other exception as `refusal(DependencyUnavailable)` = 503 `{"detail": "the datasets service failed"}`, logging the type only |
| `apps/infrx-api/tests/i/lab_control/` (new) | `test_control_routes.py` (6 fake cases), `test_control_routes_pg.py` (1 PG case, l4), `mutants.py` (19 mutants), `test_mutants.py` |

No duplicated wiring: the families are exactly `pilot._lab`'s and `_lab_checkpoints`' output
(so composition-6's `PgLabImportJobs` / `lab_releases(...)` arrive with no change here). Control
and traces keep their existing lines (anchors of the I2L and L3 mutant lists unchanged).

**No switch per family on the control unit (R237):** `_families` sets `lab_datasets/evals/
pipelines/releases=True` whatever the environment says; the unit (its enable marker and env file)
is the switch. `LAB_CHECKPOINTS` is not read either: the receiver is mounted when its credential
`LAB_CHECKPOINT_KEYS` is present (without keys no event can be verified, and requiring them would
make the whole unit refuse to start). `LAB_TEACHERS` stays the env's (default off: teacher routes
503, P-10). The App gateway keeps every Lab switch OFF (`preflight` NOT_SETTABLE, unchanged).

## Decisions

- **LDP-F1: option (b).** (a) is not one grant statement: on a fresh 0001-0052 database
  `infrx_runtime` holds EXECUTE on none of the ~100 `infrx.lab_*` functions (query below), and the
  families need D7/D8/D9 functions beyond L2's two reads; granting them widens the App runtime's
  login, against R237. So the Lab families never run on the App gateway on the box; the control
  unit serves them on `INFRX_LAB_DATABASE_URL`. No migration 0053 (0053 is also composition-6's).
- **LDP-F7 fixed here** (owned file, needed for "reaching the family's handler on the lab
  login"): `set_role=False`. Proven: E4-ON o04 `test_o04_the_control_factory_is_ready_on_its_own_login`
  now PASSes (scratch run below); `tests/i/lab_control` PG case readyz 200 on `infrx_lab_control`
  off the pooler.

## Findings

- **LCR-F1 (new, real; lab-sql):** on `infrx_lab_control` every family but control answers its
  typed 503: the login holds EXECUTE only on 0043's `lab_control_*`, `lab_provider_memberships`,
  `lab_access_grants`, `lab_deployment_aggregates` (13 functions). Sessions authenticate and
  membership resolves (never 401/404), then D7/D8/D9 calls are refused. Matrix (PG case, l4, A's
  administrator, probe `ds@1` absent):

  | family | infrx_lab_control | owner login |
  |---|---|---|
  | control `/models` | 200 | 200 |
  | datasets `versions/ds@1` | 503 `the datasets service failed` (InsufficientPrivilege, logged by type) | 404 no such Lab record |
  | evaluations `/runs` | 503 unavailable | 503 (ports unwired: WR-B4-2) |
  | pipelines `/disagreements` | 503 unavailable | 404 not_found (no dataset) |
  | teacher-batches | 503 | 503 (teachers off, P-10) |
  | releases / optimizations | 503 | 503 (WR-R4-1/2, composition-6) |

  SR-LCR-1 below. Until it lands the box's control unit serves control (+traces) only; the other
  families are typed-unavailable, never a 500.
- LDP-F3 fixed (fake case + PG case + E4-ON o05 now shows datasets `503 {"detail":"the datasets
  service failed"}` where 28c9c2cc had a 500).

## Commands (exit, counts)

Fail-first (step 1, at `d04d5bd6` over the base code): `uv run --frozen pytest -q tests/i/lab_control`
exit 1, **5 failed** (datasets 404 on every family case; `(LAB_DSN, None)` = set role; `rt` has no
`lab_datasets`). After step 2: 4/5 (datasets 500 = LDP-F3); after step 3: 5/5.

| # | Command (from `apps/infrx-api` unless noted) | Exit | Result |
|---|---|---|---|
| 1 | `uv run --frozen pytest -q tests/i/lab_control` | 0 | 6 passed, 1 skipped (PG case without a key) |
| 2 | `INFRX_D_TASK=l4 uv run --frozen pytest -q -s -m pg tests/i/lab_control` | 0 | 1 passed (matrix above) |
| 3 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab_control/test_mutants.py` | 0 | 22 passed: 19 mutants killed, 0 survivors (+ well-formed, every-case, pristine) |
| 4 | `INFRX_MUTANTS=all INFRX_LAB_API_PG=1 INFRX_D_TASK=l4 … tests/i/lab_control/test_mutants.py` | 0 | 22 passed (PG case in every copy) |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab/test_mutants.py tests/g/lab_datasets/test_mutants.py tests/l/control/test_mutants.py` | 1 | 131 passed, 29 failed = all `tests/l/control` `test_pg_mutant_is_killed` on the default d1 key held by another checkout (ForeignContainer); rerun #6 |
| 6 | `INFRX_MUTANTS=all INFRX_D_TASK=l4 … tests/l/control/test_mutants.py` | 0 | 117 passed, 0 survivors |
| 7 | E4 regression, every switch OFF: `INFRX_D_TASK=dlab uv run --frozen pytest -q tests/g tests/w tests/contracts tests/i/test_packaging.py` (at 79537d3e) | 0 | **2822 passed, 0 failed**, 27 skipped |
| 8 | `INFRX_D_TASK=dlab make api-test` (root) | 0 | 6714 passed, 0 failed, 175 skipped, 9 xfailed (1:20:47) |
| 9 | `uv run --frozen ruff check infrx/lab/control/app.py infrx/gateway/routes/lab_datasets.py tests/i/lab_control` | 0 | clean |
| 10 | `tests/l/control tests/i/lab/test_lab_packaging.py -m "not pg"` | 0 | 46 passed, 1 skipped |
| 11 | scratch `make lab-local` (below) | 2 | E4-ON FAIL, only known/named findings |

Grant query (#12, l4, fresh 0001-0052): `has_function_privilege(role, oid, 'execute')` over
`infrx.lab_%`: `infrx_lab_control` 13 granted; `infrx_runtime` 0 granted.

New cases, each named by a mutant (LANE-RULES addendum): mounted_behind_the_session (families_unmounted,
datasets_unmounted, releases_unmounted, 4x `<family>_waits_on_its_switch`), lab_login
(families_on_the_runtime_login, families_set_role, readiness_sets_role), typed_503
(store_fault_is_a_500, store_fault_is_a_400), checkpoint_receiver (checkpoints_wait_on_their_switch,
checkpoints_not_composed), lab_objects (no_objects_is_none, no_objects_answers, bucket_ignored),
own_auth_settings (families_verify_at_the_runtimes_auth, families_hold_the_service_role_key), PG
case (families_set_role, readiness_sets_role, store_fault_is_a_500/400).

## E4-ON (`make lab-local`) rerun

`lab-on` was free (lock holder pid dead) but `make lab-local` is not on this base (it arrives with
#47). Run in a **scratch detached worktree** = this branch at 79537d3e + `git merge
codex/w5-lab-deploy-prep` (02ec83ac) → 209194ea (never a branch; removed after), `make api-env`,
both pnpm installs, `make lab-local` (its stack is the lab_local design's e3l namespace, free:
lock holder dead; torn down by the runner). Verdict FAIL, only known findings:

- o04 **PASS** both cases: `test_o04_the_control_factory_is_ready_on_its_own_login` now PASS (LDP-F7 fixed; was KNOWN_FAIL).
- o05 FAIL `test_o05_every_lab_route_family_answers_a_lab_session` (the all-switches App gateway on
  `infrx_runtime`): every family `503 {"refusal":"unavailable"}` and datasets `503 {"detail":"the
  datasets service failed"}` (LDP-F1 by design under (b); LDP-F3 fixed: no 500).
  `test_o05_the_lab_routes_gateway_serves_every_family` NOT RUN (pending ports), consumer-key case PASS.
- e4-on 2794 passed / 36 failed / 14 skipped: the identical 36 failing ids as 28c9c2cc's run (set diff empty; WR-LDP-5 pins + LDP-F4).
- o01 o02 o06 PASS, o03 o07 NOT RUN (as before), journey:datasets PASS.

Rerun command after #47 and this lane merge: `make lab-local` (≈45 min, lab-on + e3l free).

## Wiring requests (exact)

**WR-LCR-1 (Makefile, coordinator):** api-mutants line 30 →
`cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab_pipeline/test_mutants.py tests/i/lab_rollout/test_mutants.py tests/i/lab_control/test_mutants.py`.
Test: `make api-mutants` runs 22 more cases, 0 survivors.

**WR-LCR-2 (infra/lab/app, I2L owner): the control site's body bound per family.** The site's 1 MiB
`request_body` would refuse dataset imports (up to 64 MiB, `lab_datasets.MAX_BODY_BYTES`) now that
the unit serves them. Validated with the pinned Caddy (`caddy validate`, network none, composed with
`deploy/Caddyfile` + the import line): `Valid configuration`.

```diff
--- a/apps/infrx-api/deploy/lab/app/lab-control.caddy
+++ b/apps/infrx-api/deploy/lab/app/lab-control.caddy
@@ -8,10 +8,6 @@
 {$INFRX_LAB_CONTROL_SITE:lab-control.callbill.ai} {
-	request_body {
-		max_size 1MiB
-	}
-
 	# The control service down, restarting or rolled back is "Lab unavailable", never an
@@ -26,7 +22,19 @@
 		}
+		# WR-LDP-2: the unit serves every Lab family; a dataset import is one bounded request
+		# of up to 64 MiB (lab_datasets MAX_BODY_BYTES), every other Lab call stays at 1 MiB.
+		@datasets path_regexp ^/lab/v1/providers/[^/]+/datasets/
+		handle @datasets {
+			request_body {
+				max_size 64MiB
+			}
+			reverse_proxy {$INFRX_LAB_CONTROL_UPSTREAM:127.0.0.1:8003}
+		}
 		handle /lab/v1/* {
+			request_body {
+				max_size 1MiB
+			}
 			reverse_proxy {$INFRX_LAB_CONTROL_UPSTREAM:127.0.0.1:8003}
 		}
```
Test (tests/i/lab): the live edge case posts 2 MiB to `/lab/v1/control/register` (413) and to
`/lab/v1/providers/<p>/datasets/imports` (reaches the stub). `lab-control.callbill.ai` already
proxies every `/lab/v1/*` family; no other site or unit change is needed (the unit's `--memory 1g`
holds one 64 MiB body).

**WR-LCR-3 (infra/lab/app/lab.json + README, I2L owner):** declare the unit's new optional names
under `env.lab-control` (and a README row each, which `test_i2l__secret_names_only…` requires):
```json
{"name": "LAB_S3_BUCKET", "exposure": "server", "purpose": "the Lab objects (the Lab workers' bucket, instance-role credentials) the datasets, pipelines and releases families read and write; unset = those uses answer 503 (NoObjects)"},
{"name": "LAB_S3_ENDPOINT", "exposure": "server", "purpose": "that bucket's endpoint (optional)"},
{"name": "LAB_CHECKPOINT_KEYS", "exposure": "secret", "purpose": "the checkpoint receiver's key directory (WR-B3-2); set = POST /lab/v1/checkpoints is served by this unit, unset = not mounted"}
```

**WR-LCR-4 (runbook `research/plan/consumer-v1/08-lab-internal-testing-rollout.md`, on
codex/w5-lab-deploy-prep / #47; proposed, not edited):**
- §0 table row `/datasets, /annotations, …`: "Served on the box by" → `the control service
  (infrx-lab-control, :8003) - every family, WR-LDP-2 (lab-control-routes)`; "Internal testing
  today" → `datasets/annotations/training/evaluations/releases answer "unavailable" on the box until
  SR-LCR-1 grants infrx_lab_control their D7/D8/D9 functions (LCR-F1), and evals/pipelines/releases
  further until WR-B4-2 / WR-LAB2-4 / WR-R4-1/2 / WR-P4B-1; never a 500 (LDP-F3 fixed)`.
- §0 "E4-ON FAILs" paragraph: LDP-F1 is resolved by design (option b, R237): the App gateway never
  serves the Lab; LDP-F3 fixed.
- §3 L5 note (lines ~139-147): LDP-F7 fixed (`set_role=False`); `control_database_url` may be the
  direct or the :6543 DSN; L5's proof is E4-ON o04's login case (PASS).
- 40-lab-control.sh SPEC (L5): add optional `LAB_S3_BUCKET:=<Lab bucket>` (the workers' bucket)
  so datasets/pipelines/releases reach the Lab objects; never `LAB_CHECKPOINT_KEYS` for internal
  testing (§5 rule 3).
- §6 Vercel env row: `LAB_DATASETS_API_URL`, `LAB_EVALS_API_URL`, `LAB_PIPELINES_API_URL`,
  `LAB_RELEASES_API_URL` = `https://lab-control.callbill.ai` (WR-LDP-2 landed); the "after WR-LDP-2
  (unset until then)" clause removed.
- §8 open items: WR-LDP-2 done (this lane); LDP-F7 done; add SR-LCR-1 (LCR-F1) and WR-LCR-2.

**WR-LCR-5 (tests/integration/lab_local, #47's owner):** drop `O04_LOGIN` from `KNOWN_FAIL`
(`tests/integration/lab_local/mutants.py:193`; o04 login PASS above); add an o05 case judging the
families on the control factory on `infrx_lab_control` (`on.control`, the same `FAMILIES`, pending
families `split_pending` as today, plus LCR-F1's families NOT RUN[SR-LCR-1]) and mark
`test_o05_every_lab_route_family_answers_a_lab_session` (the all-switches App gateway) as the R237
"never on the box" configuration (KNOWN_FAIL O05_ALL stays, reason LDP-F1 option (b)).

**SR-LCR-1 (lab-sql, Lab-only additive migration, next free number, local only):** grant
`infrx_lab_control` EXECUTE on the route halves the families call (worker-only claims excluded):
D7 `lab_resolve`, `lab_publish`, `lab_list_datasets`, `lab_accessible_samples`, `lab_dataset_uses`,
`lab_register_source`, `lab_import_job_enqueue`, `lab_import_job` (0051), `lab_create_run`,
`lab_run_status`, `lab_run_results`, `lab_cancel_run`, `lab_experiments`, `lab_put_experiment`,
`lab_evaluator`, `lab_put_evaluator`, `lab_eval_report`, `lab_variant_comparisons`,
`lab_receive_checkpoint`, `lab_checkpoint_transition`; D8 `lab_label_append`, `lab_label_events`,
`lab_external_run_get`, `lab_external_run_move`, `lab_run_reserve`, `lab_run_release`,
`lab_run_settle`, `lab_checkpoint_listing`, `lab_checkpoint_event(s)`, `lab_checkpoint_decide`,
`lab_checkpoint_reject`, `lab_checkpoint_subscribe`, `lab_checkpoint_subscriptions`,
`lab_checkpoint_decisions`, `lab_teacher_*` (route half); D9/0043 `lab_releases_in`,
`lab_release`, `lab_release_proposals`, `lab_propose_release`, `lab_decide_release_proposal`
(+ composition-6's 0053 reads). lab-sql owns the exact set via its PG role matrix. Test: this
lane's PG case with the matrix's lab column equal to the owner column.

## Proposed ruling (unnumbered; next free after #47 = R238)

"The Lab control unit (`infrx.lab.control.app`) is the only process that serves `/lab/v1/*` on
the box: every family through `pilot._lab`'s composition on `INFRX_LAB_DATABASE_URL` (never
`set role`), no `LAB_*` switch read for a family (the unit is the switch; the checkpoint receiver
is mounted by its key directory). The App gateway's Lab switches exist for local compositions only.
Every family's store fault is its typed 503, never a 500."

## Open issues

- LCR-F1 / SR-LCR-1 (above); WR-LCR-2..5 to their owners.
- `make api-test` on the default key d1 is held by another checkout; run on dlab (#8).

## Estimate (remaining for I2L's WR-LDP-2 slice)

optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium. Basis: lane work done with 0
survivors; remaining = coordinator merge + WR-LCR-1..5 (one small diff each, ~15 min) + SR-LCR-1
(lab-sql, analogue D10-0025 1/2/5 h, not counted) + one `make lab-local` rerun (~45 min).
