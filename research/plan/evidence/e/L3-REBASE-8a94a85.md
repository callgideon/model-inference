# L3-REBASE — layer-3 harness pins and role matrix on D10's 0019–0021

Base `496fdc84` (integration tip), code head `8a94a855`, branch `codex/l3-rebase`. Task E2C
(in review), micro-lane. Services: the harness's own `e2` namespace (`run.py`), torn down.

## Changed paths
- `tests/integration/test_harness.py` — migration pin + 0019–0021 (filename order still asserted).
- `tests/integration/pgstate.py` — role matrix: `RELATIONS` + `infrx.content_objects`,
  `infrx.job_readiness` (SERVICE, 0019:950-955); `FUNCTIONS` + 34 SECURITY DEFINER functions
  from 0019–0021; `INVOKER_FUNCTIONS` + 7 pure helpers revoked from everyone; `SERVICE_WRITES`
  definer-only set + `media_uploads`, `content_objects`, `job_readiness`; new `login_rows()`
  (`LOGINS`, `RUNTIME_FUNCTIONS`) pins `infrx_runtime` / `infrx_monitor` (12 rows).
- `tests/integration/mutants.py` — e2m74, e2m75, e2m76 (layer 2).
- `tests/integration/backend/recovery/test_restore.py` — bk01h.

## Re-baselined (each derived from migration text, then measured)
| case | old | new | justification |
|---|---|---|---|
| test_harness migration pin | 0001–0018 | 0001–0021 | D10 merged 0019/0020/0021 |
| E3B-RLS-W-infrx.media_uploads-service_role | INSERT,UPDATE,DELETE | none | 0019:956-959 `revoke insert, update, delete on infrx.media_uploads from service_role`; the `upload_*` boundary is the writer (R128; 0019 ROLLBACK note) |
| bk01h restored `read_result` | read of `kept` any time; read of a fresh result on the queued job returns it | read under the test clock frozen 1 min before the persisted `result_expires_at` returns `kept`; at that instant `result_expired`; the fresh write lands (row read as owner) and its read is `result_pending` | 0020:439-462 `read_result` requires `settled_at` and `now < result_expires_at` (R125); fixture persists 2026-09-22T00:05Z |

New rows (no old expectation): per 0019:964-986, 0020:470-489, 0021:420-448 — SERVICE:
`admit_ready`, `claim_preparation_ready`, `content_register`, `content_references`,
`readiness_doc`, `readiness_cutover_check`, `upload_{create,acknowledge_put,complete,abort,resolve,expire}`,
`content_{candidates,claim,tombstone,acknowledge_delete}`, `register_existing_database_content`,
`usd_price`, `jobs_result_expiry_guard` (0004:53 default privilege, 0021 revokes nothing);
NOBODY: `bind_source`, `check_pinned_capability`, `content_objects_guard`, `register_content`,
`upload_row`, `content_{referenced,row,recheck}`, `scrub_content`, `job_results_guard`,
`register_database_content`, `resolve_usd_revision`, `public.consumer_org`; BROWSER
(authenticated + service_role): `public.consumer_jobs`, `public.consumer_job_result`.
Logins (0021:458-560, R127): attributes all false (NOLOGIN, NOINHERIT); member of nothing; the
only member is the creator's PG16+ implicit ADMIN (`postgres`, no INHERIT/SET); role-default
config; runtime executes exactly 0021's 42 functions, SELECT on 13 relations + SELECT,INSERT on
`staged_media`/`job_media`, column UPDATE only `jobs.updated_at`; monitor executes nothing,
SELECT on the two reconciliation views, and exactly 0021:543-549's column grants.
Every API-role expectation derived from the text matched the measured catalog on the first run
(880/880) — no widening/narrowing observed for anon/authenticated/service_role.

## Commands
| cmd | exit | summary |
|---|---|---|
| `run.py --layer 3 --only-suites --no-mutants --keep` at base (fail-first) | 1 | rls FAIL (731 cases, 1 failed: media_uploads service_role); backend 229/3 failed (ob10, bk01h, db06); suites 177/3 failed (migration pin, role matrix, completeness: 2 relations + 34 functions unrowed) |
| `pytest -q tests/integration --ignore=tests/integration/backend` (stack up, head) | 0 | 180 passed |
| `pytest -q tests/integration/backend/recovery/test_restore.py -k bk01` (stack up) | 0 | 25 passed (bk01b/c/d detections included) |
| `mutants.py --layer all --only e2m74/e2m75/e2m76` | 0 | each killed (1 failed selected case); control e2c02 survived |
| `run.py --layer 3 --only-suites --no-mutants` at head | 1 | preflight/services/migrate PASS; **rls PASS 880 cases**; backend 231 passed, 1 failed (ob10, not owned — F1); engine, suites, teardown PASS |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

db06 failed once in the base run while a concurrent bk01 run shared the E2 database (my
own overlap); it passed standalone and in the head run.

## Findings
- **F1 (medium, gate):** `test_observe::test_i3b_ob10_every_rule_and_panel_names_a_declared_metric`
  fails on the tip: `families with no panel: infrx_large_body_slots_in_use,
  infrx_large_body_slots_limit, infrx_large_body_refused_total, infrx_intake_drained_total`
  (declared in `apps/infrx-api/infrx/observe/metrics.py`, emitted by `gateway/routes/intake.py`,
  G7 merge 739c0414). Fix: I8/G7 add dashboard panels (infra/observe) for the four families.
  Not a privilege issue; outside this lane's paths.
- **F2 (low, consistency):** 0021 leaves `infrx.jobs_result_expiry_guard()` with 0004's default
  service_role EXECUTE while 0019/0020 revoke their guards from everyone. Harmless (a trigger
  function cannot be called outside a trigger) — pinned as SERVICE; fix: revoke in D10's 0022
  and flip the row to NOBODY.
- **F3 (info, known interim):** `infrx_runtime` still executes `infrx.admit(jsonb)` and
  `infrx.claim_preparation(jsonb)` (the unmarked doors) — R123's interim clause; pinned in
  `RUNTIME_FUNCTIONS`; D10-FOLLOWUP's 0022 revoke must remove them from that tuple.
No unintended widening/narrowing found for anon, authenticated, service_role or the logins.

## Wiring requests
None. Note for D10-FOLLOWUP: 0022 will break the migration pin (add `0022_*`) and, if it
revokes the unmarked doors / the expiry guard, `RUNTIME_FUNCTIONS` and the guard row.

## Estimate
Remaining for this lane: optimistic 0 h, likely 0.25 h (review nits), pessimistic 1 h (0022
re-baseline); confidence high; basis: layer-3 rls stage green at head.
