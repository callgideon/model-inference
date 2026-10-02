# api-reads (AP-02) — handback evidence at ddac364

- Lane: api-reads, wave 7 (LW7). Task AP-02 (02a, 02b, 02c). Key `ap2` (PostgreSQL 57552).
- Base: `cd9f517c` (keystone R270–R271). Branch `codex/w7-api-reads`. Head of the work: `ddac3648`.
- Commits: `939eb698` (02a), `12c835a9` (02b), `86c01a0e` (02c), `ddac3648` (parity + OpenAPI documentation case).

## Changed paths (all owned)

- `apps/infrx-api/infrx/console/reads.py` (new): `ConsoleReads`, the read repositories and R270 DTOs.
- `apps/infrx-api/infrx/console/cursor.py` (new): HMAC opaque keyset cursors scoped actor|operation|filters|sort.
- `apps/infrx-api/infrx/gateway/routes/console_reads.py` (new): `register(app, rt)`, 12 GET routes.
- `apps/infrx-api/tests/ap02/` (new): `conftest.py`, `world.py` (seed), `test_cursor.py`, `test_units.py`, `test_credits_pg.py`, `test_requests_pg.py`, `test_projections_pg.py`, `test_parity_pg.py`, `parity_harness.ts`, `mutants.py`, `test_mutants.py`.
- `infrx/console/__init__.py` is NOT created (api-identity owns it); `infrx.console` imports as a namespace package until AP-01 merges, and works the same afterwards.

## What exists

Routes (GET only, no-store, R270 envelope on every failure, actor only from `rt.actors`, mounted only when `rt.console_reads` is set):
`/console/v1/credits`, `/credit-ledger`, `/legacy-statement`, `/requests` (filters `model`, `key_id`, `from`, `to`), `/requests/{id}`, `/requests/{id}/result`, `/keys`, `/account/members`; operator `/operator/v1/accounts`, `/wallet-drift`, `/unknown-usage`, `/audit`.

How a read runs: one read-only transaction, `set local role authenticated`, the verified user id as JWT subject (both claim forms), over the existing views and functions only (`console_wallet_summary`, `console_credit_ledger`, `consumer_credit_ledger`, `console_legacy_usd_statement`, `consumer_jobs`, `consumer_job_result`, `api_keys`, `org_members`/`profiles`, `console_credit_wallets` + `console_admin_orgs`, `operator_wallet_drift`, `operator_unknown_usage`, `operator_audit`). The database guards apply as they do for the App's PostgREST session today. The org id is the actor's, bound last. Operator reads require `Actor.operator` AND `public.is_operator()` inside the transaction. A suspended account keeps every read (R33).

- Money: R270 `Money` strings from the database text, checked by `money_units.parse_amount`. "spent" = Σ(non-debit) − ledger_total computed with `money_units` (Decimal); null past 100 entries or with no wallet, never a float.
- Pages: 25 by default, 1..100 or 422 `invalid_request`. Each page asks for `limit+1` rows, capped at 100. At the cap a full page still carries a cursor (credit-reads.ts `rpcPage`'s rule). The cursor resumes after the last row shown and wraps the SQL function's own keyset.
- Result: served only when the read outcome is `available`. pending/held_unknown → 409 `result_pending`, no_result → 404, no persisted expiry → 503, expired (by the database clock) → 410 `result_expired`. This is request-view-model.ts `RESULT_STATUS` as R270 envelopes.
- Failures: a database error the SQL names (`invalid_cursor`, `not_found`, `result_*`) maps to its domain error; 42501 → 403; anything else, unreachable included → 503 `unavailable` (retryable). A malformed row → 500 `internal_error`. No SQL text leaves.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/ap02/test_cursor.py` (before `cursor.py`) | 1 | red: collection error (module absent) |
| `INFRX_D_TASK=ap2 … pytest -q tests/ap02/test_credits_pg.py` (before the route module) | 1 | red: 17 failed |
| `INFRX_D_TASK=ap2 … pytest -q tests/ap02/test_requests_pg.py tests/ap02/test_units.py` (before the 02b routes) | 1 | red: 11 failed, 11 passed (the unit half passed: see Deviations) |
| `INFRX_D_TASK=ap2 … pytest -q tests/ap02/test_projections_pg.py` (before the 02c routes) | 1 | red: 15 failed |
| `INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/ap02` | 0 | 72 passed |
| `INFRX_D_TASK=ap2 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/ap02 --deselect …test_mutant_is_killed` | 0 | 71 passed, 1 deselected (real `supabase/postgres` image) |
| `uv run --frozen pytest -q tests/ap02` (no key) | 0 | 27 passed, 45 skipped visibly ("PostgreSQL only on the ap2 task-local key") |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/ap02/test_mutants.py` | 0 | 65 passed: 63 mutants (22 unit + 41 PostgreSQL) killed, 0 survivors; `well_formed` and `every_case` green; 790 s |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 458 errors (baseline 458): no new errors |
| `tests/integration/test_makefile_mutant_lists.py` | 1 | expected until WR-AP02-2: "api-mutants names apps/infrx-api/tests/ap02/test_mutants.py 0 times" |

Failed-then-passed during the work: the suspension fixture (two check constraints on `organizations`). The parity audit order tied on a frozen clock: operator-reads.ts orders by `at` alone, and the case now advances the clock; the API breaks ties by id. `ap02_cursor_padding_lost` survived until the round-trip case covered every base64 padding residue. `ap02_route_undocumented_errors` died by KeyError until the case asserted instead.

## Parity (02a exit)

`test_parity_pg.py` runs `parity_harness.ts` under node. The harness loads the App's current `credit-reads.ts`, `request-reads.ts`, `request-view-model.ts` and `operator-reads.ts` unchanged. Its client does what PostgREST does: `set local role authenticated`, both JWT claim forms, and `coalesce(json_agg(t), '[]')` or `to_json(fn())` rendering through `psql` on the ap2 database. The case compares the result field by field with the routes' answers on the same seeded rows:
- wallet totals, "spent" and every ledger entry;
- the legacy USD statement;
- every job: all fields, its unit, its read outcome, and the result status and text;
- key options;
- operator accounts, unknown usage, drift and audit.

Everything is identical. The seed in `world.py` is the D admission scenario plus jobs made through the real admit/claim/put_result/terminalize paths: settled with a result, held_unknown, failed/released, running/held, legacy USD, and a foreign consumer's job. It also includes an operator adjustment through `operator_adjust_credit`.

## Wiring requests (coordinator; not applied)

**WR-AP02-1 — the switch and the composition (depends on AP-01's `rt.actors`).**
- `apps/infrx-api/infrx/config.py`, in `DeploymentSettings` after `lab_teacher_url`:
  ```python
      # WR-AP02-1 (AP-02): mount the console read routes (/console/v1/* reads, /operator/v1/*
      # reads) over infrx.console.reads.ConsoleReads. Off by default: no such route exists. Each
      # read does `set local role authenticated` (PostgREST's session), so CONSOLE_DATABASE_URL
      # must be a login that may (Supabase's `authenticator`), never `infrx_runtime`;
      # CONSOLE_CURSOR_SECRET (>= 16 bytes) is read by name, never in a repr.
      console_reads: bool = False
      console_database_url: str = field(default="", repr=False)
      console_cursor_secret: str = field(default="", repr=False)
  ```
- `apps/infrx-api/infrx/gateway/pilot.py` `build_ingress_deps`, after `rt.lab_datasets = …`:
  ```python
      # AP-02 (WR-AP02-1): the console reads mount over this, and only when enabled.
      rt.console_reads = _console_reads(rt, deployment) if deployment.console_reads else None
  ```
  plus, at module level:
  ```python
  def _console_reads(rt, deployment):
      from ..console.reads import ConsoleReads
      from ..state.jobstore import connector
      secret = deployment.console_cursor_secret.encode()
      if not deployment.console_database_url or len(secret) < 16:
          raise RuntimeMisconfigured(rt.mode, ("CONSOLE_DATABASE_URL", "CONSOLE_CURSOR_SECRET"))
      return ConsoleReads(connector(deployment.console_database_url, set_role=False), secret)
  ```
  `set_role=False` is deliberate: the login itself switches role per transaction (`SET LOCAL`, which is also safe on the transaction pooler).
- `apps/infrx-api/infrx/gateway/app.py`: import `console_reads` from `.routes` and append it to `ROUTERS` after `lab_checkpoints` (before `metrics`). Extend the comment: "AP-02's console reads (`CONSOLE_READS`, default off; WR-AP02-1)."
- Composed test (`tests/g/test_composition.py` style, INFRX_D_TASK=ap2):
  - with `CONSOLE_READS=1`, `CONSOLE_DATABASE_URL=<ap2 dsn>`, `CONSOLE_CURSOR_SECRET=<16+ bytes>` and AP-01's `SessionActors` on `rt.actors`, `create_app()` answers `GET /console/v1/credits` 200 no-store for a seeded session (`tests/ap02/world.py`);
  - with the switch off, it answers 404 and `[r for r in app.routes if r.path.startswith(("/console/", "/operator/"))] == []`;
  - with it on but the secret short or the DSN missing, startup raises `RuntimeMisconfigured` naming both variables.

**WR-AP02-2 — Makefile.** Under `api-mutants`, a line of its own (the D harness's host lock keeps the PostgreSQL list in its own process):
```
	# AP-02's console reads (wave 7): PostgreSQL half needs Docker, skips visibly without it; task-local key ap2
	cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/ap02/test_mutants.py
```
Composed test: `tests/integration/test_makefile_mutant_lists.py` turns green.

## Schema requests

None: every read is an existing view or function. Deployment prerequisite (not DDL): the console read DSN must log in as a role that can `SET ROLE authenticated`. Supabase's `authenticator` (or `postgres`) can; the runtime's dedicated `infrx_runtime` cannot, by design (0021/0024 grant `consumer_*` to `authenticated`, never `infrx_runtime`).

## Deviations

`reads.py` was written whole before the 02b/02c route tests, so the unit half of the 02b red run already passed. Each slice's route tests were still red before its routes existed, and every decision has a killing mutant.

## Open items

- A mutant on the JWT subject alone survives by design: both claim forms are set on purpose (one per image), so no single-form edit is observable on either image. Both images pass the suite.
- operator-reads.ts orders the audit by `at` without a tiebreak, so equal instants list in an unstable order. The API orders by (at, id). Worth fixing when AP-09 moves the page to the client.
- `/console/v1/requests/{id}/result` answers a success with no persisted expiry with 503, as the App does today (`unavailable`). Its `retryable: true` is the R270 table's value for `DependencyUnavailable`. Proposed ruling (unnumbered): a permanent "unavailable" read outcome should get its own non-retryable code.

## Estimate (remaining, lane effort)

Optimistic 0.5 h · likely 1.5 h · pessimistic 4 h · confidence medium. Basis: one review round on 3 modules and 72 cases, plus the composed test once AP-01's `SessionActors` and WR-AP02-1 land. The pessimistic case is a production login that cannot `SET ROLE authenticated`, which needs a deployment decision rather than code.
