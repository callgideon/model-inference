# api-actions (AP-03, wave 7 batch 1) — evidence at 75b8d25

Lane `api-actions`, branch `codex/w7-api-actions`, worktree `.claude/worktrees/codex-w7-api-actions`,
base `cd9f517c` (keystone R270–R271), head `75b8d25e`. Task-local key `ap3` (PostgreSQL 57553,
container `infrx-ap3-postgres`); d1/55432, hosted Supabase, the box, AWS and Vercel untouched. No
migration (0001–0059 unchanged; none allocated to this lane). Nothing mounted: every route needs
`rt.console_actions`, which no composition sets (default OFF).

## Changed paths (all owned)

- `apps/infrx-api/infrx/console/actions.py` (new) — wire models + `ConsoleActions` repository + `submit_feedback`.
- `apps/infrx-api/infrx/gateway/routes/console_actions.py` (new) — `POST /console/v1/keys`, `DELETE /console/v1/keys/{key_id}`, `POST /console/v1/signup-grant/claim`, `POST /console/v1/requests/{request_id}/feedback`; `ControlRoute` (R270 rendering of FastAPI validation errors and escaped errors).
- `apps/infrx-api/infrx/gateway/routes/operator_actions.py` (new) — `POST /operator/v1/credit-adjustments`, `/suspensions`, `/key-revocations`.
- `apps/infrx-api/tests/ap03/` (new) — `test_routes.py` (14), `test_actions.py` (5), `test_actions_pg.py` (16, `pg`, ap3 only), `racer.py` (one API instance per OS process), `mutants.py` (23 unit + 23 PG), `test_mutants.py`.

`infrx/console/__init__.py` is AP-01's path; `infrx/console/` works as a namespace package until it lands.

## Slices

| Slice | Done | How (reuse) |
|---|---|---|
| 03a keys | yes | Secret/hash/prefix = `operations.service.new_secret`/`hash_key`/`PREFIX_CHARS` (already the App's `keys.ts` shape). Durable identity = `infrx.audit_entries.idempotency_key` (`_once` pattern, no migration): scope `key.create:<org>:<sha256(Idempotency-Key)>`, key id = `stable_id(scope)`, one `pg_advisory_xact_lock` per scope; first success 201 + secret; replay 200 + same key, `secret:null, secret_returned:false`; another name → 409. Eligibility = `consumer_may_create_key()` + `is_org_owner(org)` with the actor bound as JWT subject; suspended → 403 `suspended`. Revoke = `infrx.revoke_key` after an org/consumer-scoped row lock; allowed while suspended; reused key on another key → 409 (audit unique index). Audit action is `admin_key_issue` (0009's CHECK has no consumer action; `after.operation = key.create`, actor `console:<uid>`). |
| 03b grant | yes | `signup.CLAIM`/`GRANT_ROW` (PgSignup's statements) with the App's campaign `consumer-v1`, as the Actor's own individual (no body: a body is 422). 200 `{status, credit: Money, granted_at}`; denials are a `status` (unverified/identity_reused/rollout_hold/retired), never credit. |
| 03c feedback | yes | `rt.feedback.accept` (D6F) with server provenance: actor org/individual, operation `feedback.submit`, canonical-signal digest; `rt.feedback` None → 503 `unavailable`. Ownership = `accept_feedback`'s job/org rule. |
| 03d operator | yes | Wraps `operator_adjust_credit`/`operator_set_suspension`/`operator_revoke_key` with mandatory reason + Idempotency-Key, as `authenticated` (0025's only grantee) with the operator bound as subject. Non-operators refused at the route, at the repository (unit case calls it with a consumer actor and a connection factory that fails if dialled) and by `console_operator` (forged `operator=True` case). |

## Commands (exit codes, counts)

| Command | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=ap3 uv run --frozen pytest -q tests/ap03` before any implementation | 2 | RED: `ModuleNotFoundError: No module named 'infrx.console'` (3 collection errors) |
| first PG run after the implementation | 1 | RED: operator adjustment `Forbidden` — 0025 grants EXECUTE to `authenticated` only; fixed by `set local role authenticated` (and 42501 mapped to 403 only for `forbidden:`) |
| manual mutant: advisory lock removed, race case | 1 | killed: `[201, 409, 409, 409, 409, 409]` (proves the race really contends) |
| `INFRX_D_TASK=ap3 uv run --frozen pytest -q tests/ap03 -k "not mutant_is_killed and not false_kill"` | 0 | 37 passed (14 route + 5 repository + 16 PG + 2 list-shape) |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap3 uv run --frozen pytest -q tests/ap03/test_mutants.py` | 0 | 50 passed: 23/23 unit mutants killed, 23/23 PG mutants killed, `test_every_case_is_covered_by_a_mutant` + well-formed + 2 runner self-tests green (244 s) |
| `INFRX_D_TASK=ap3 uv run --frozen pytest -q tests/contracts tests/g tests/i tests/ap03` (at 7d35aeee, edits landing mid-run) | 1 | 2902 passed, 23 skipped, 1 xfailed; 1 failed = `tests/ap03/test_mutants.py::test_the_list_is_well_formed` (files edited during the run; green on rerun above) |
| same tracks without `INFRX_D_TASK` | 1 | `tests/contracts/v2/test_v1_projection_pg.py` HarnessBusy on d1 (another checkout holds it; nothing touched) — environment, not this lane |
| `make api-lint` | 0 | All checks passed (ruff 0.15.12; no per-file ignores added) |
| `make api-typecheck` | 0 | `pyright: 458 errors (baseline 458)` — no new errors; lane files 0 errors |
| `tests/integration/test_makefile_mutant_lists.py` | 1 | 7 failed: `tests/ap03/test_mutants.py` named 0 times — expected until WR-AP03-1; with the patch applied (then reverted) 7 passed |

## Wiring requests

- **WR-AP03-1 (Makefile, api-mutants)** — after the `tests/d/test_code_mutants_lw9.py` line:
  ```
  	# AP-03's lists (api-actions, wave 7): the unit list, and the PostgreSQL list (needs Docker, skips visibly without it); task-local key ap3
  	cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap3 uv run --frozen pytest -q tests/ap03/test_mutants.py
  ```
  Composed test: `tests/integration/test_makefile_mutant_lists.py` 7 passed with it (run at 75b8d25).
- **WR-AP03-2 (switch, AP-00's settings)** — `infrx/config.py` DeploymentSettings: `console_actions_api: bool = False` (`CONSOLE_ACTIONS_API`).
- **WR-AP03-3 (composition)** — `infrx/gateway/pilot.py` `adapters_from_env`: `**({"console_actions": ConsoleActions(connect)} if settings.deployment.console_actions_api else {}),` (import `from ..console.actions import ConsoleActions`); `build_ingress_deps(..., console_actions=None)`: `rt.console_actions = console_actions if deployment.console_actions_api else None`; refuse `CONSOLE_ACTIONS_API` without AP-01's `rt.actors` (RuntimeMisconfigured). `infrx/gateway/app.py`: import `console_actions, operator_actions` and add them to `ROUTERS` after `feedback`. Composed test: `create_app()` with the switch off → `POST /console/v1/keys` 404; with it on, a recording `console_actions` and `StaticActors` → 201, and `tests/g/test_startup.py` docs-absent assertions unchanged.
- **Connection requirement (with WR-AP03-3)** — the repository needs a login that may `set role service_role` and `set local role authenticated` (the broad `postgres` login does; 0021's dedicated `infrx_runtime` does not). If the pilot pool is on `infrx_runtime`, compose `ConsoleActions` over a second connector on the broad login, or file the grant as a schema request (see below). Transactions are explicit BEGIN/COMMIT on `execute` only, so the pool's `_Pooled` shape works unchanged (tested through an execute-only proxy that asserts the connection returns idle).

## Schema requests

None for the default composition. Conditional (only if the coordinator keeps the API on `infrx_runtime`): `grant authenticated to infrx_runtime`, `insert` on `public.api_keys` and `infrx.audit_entries`, `execute` on `public.consumer_may_create_key()`, `public.is_org_owner(uuid)`, `public.claim_signup_grant(uuid, text, uuid)`, `infrx.revoke_key(uuid, text, text, text)`, in 0060 (api-schema).

## Open items

- Feedback rows from the console route carry `rt.feedback`'s channel (`api` in the pilot composition); 0038's console door stamps `console`. Composing a console-channel `PgFeedbackService` for this route is a one-line wiring choice (`ConsoleActions` could carry it); not done to keep `rt.feedback` the single switch the brief names.
- `replayed` on feedback stays false (`FeedbackService.accept` does not report it; G4F's WR-G4F-2 gap).
- A request body that is not JSON at all gets FastAPI's 422 before the actor dependency runs; any JSON body is validated only after identity (tested).
- `ControlRoute` (R270 rendering of validation errors) is local to AP-03's two modules; AP-00 may lift it into `gateway/control.py`.
- An operator-audience API key is refused for operator mutations (the 0025 guard authorizes a session subject); operator CLI keys keep `operations/cli.py`.

## Estimate (remaining, AP-03)

optimistic 1 h / likely 2 h / pessimistic 5 h, confidence medium — basis: code, tests and both mutant lists are done and green on ap3; what remains is review fixes plus composing WR-AP03-2/3 at merge (one composed `create_app` test) and the login decision for the connection requirement; the pessimistic case is the `infrx_runtime` grant path through 0060.
