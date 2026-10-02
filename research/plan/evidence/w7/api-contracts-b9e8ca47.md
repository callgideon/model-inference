# api-contracts (AP-00 slices 00a, 00b, 00c): b9e8ca47

Lane api-contracts of wave 7 (LW7), branch `codex/w7-api-contracts`, base `cd9f517c`, head `b9e8ca47`
(commits 57357d1a 00a/00b, 3f644b03 00c, b9e8ca47 00c boundary-test follow-up). Key: none (no Docker used).

## Changed paths

- 00a/00b (owned): `apps/infrx-api/infrx/contracts/openapi/{__init__,export,inventory}.py`, `.../openapi/baseline.json`,
  `apps/infrx-api/openapi/{consumer,lab-control}.json` (generated), `apps/infrx-api/tests/contracts/test_openapi_export.py`,
  `apps/infrx-api/tests/contracts/mutants.py` (12 `ap00_*` mutants, runner target), `research/plan/api-lifecycle/evidence/route-inventory.{json,md}` (generated).
- 00b worked example (named by the brief, outside the listed paths): `infrx/gateway/lab_auth.py` (`json_object`, `Refusal`,
  `REFUSED`, `refusal_route`), `infrx/gateway/routes/lab_control.py` (typed handlers), and the anchors those moved:
  `tests/g/lab_control/{mutants,test_lab_control}.py` (2 added assertions in existing cases), `tests/l/control/mutants.py` (2 anchors),
  `tests/contracts/test_mutants.py` (RECORD_TESTS gains the new module), `tests/contracts/lab/frozen_contracts.json` (4 new files pinned + a revision row).
- 00c (owned): `packages/api-client/` (package.json, pnpm-lock.yaml, tsconfig.json, `src/{consumer,lab}.ts` generated, `src/transport.ts`,
  `tests/{transport,package}.test.ts`, `tests/mutants.mjs`), `apps/app/lib/api/index{,.test}.ts`, `apps/lab/lib/api/index{,.test}.ts`.

## What was built

- **00a** `inventory.build(compositions)`: every mounted (method, path) of `consumer` (every consumer switch on), `consumer-launched`
  (defaults, all OFF) and `lab-control` (traces + checkpoints on): compositions, audience, envelope (openai / refusal / r270 / none), request and
  response schema state (typed / none / raw), today's consumer; 44 web actions of both apps (maps §B) with transport and target operation, 26
  flagged (product table/RPC or service role); contracts.md §3's 67 operations, 13 `existing`, 54 `target`; 67 mounted routes in all.
- **00b** `export.document`: `get_openapi` over every mounted FastAPI route (hidden ones too: `/metrics`), operationId = method + path
  (unique by construction), security from `FAMILIES` (ApiKeyAuth `/v1/*`, SessionBearer `/lab/v1/*` `/console/v1/*`, SessionBearer|OperatorKey
  `/operator/v1/*`, CheckpointSignature `/lab/v1/checkpoints`, `[]` for health/metrics/`/v1/models`; `/auth/v1/*` must declare its own; an
  unclassified family refuses the export). `baseline.json` = the 60 schema-less routes (consumer 16, lab-control 44); the test pins 60 and
  requires `legacy == baseline`. lab_control (8 operations) is typed: pydantic bodies, path/query params and `response_model`s as FastAPI
  parameters, identity a `Depends`, mounted with `lab_auth.refusal_route(rt)` on the app's table (FastAPI 0.141's `include_router` leaves an
  `_IncludedRouter` in `app.routes`, so routes are added directly like lab_datasets). The adapter buffers the body bounded (16 KiB, intake
  deadline) and withholds an unreadable one so the identity dependency answers first; FastAPI's validation error is `{refusal: invalid}`.
  Wire unchanged: tests/g/lab_control and tests/l/control/test_operations.py green unedited (two assertions added, both green on the base too).
- **00c** `packages/api-client`: openapi-typescript 7.13.0 + typescript 5.9.3 pinned devDependencies of the package only; `transport.ts`
  (`createClient<paths>`: `call(method, path, {params, query, body, idempotencyKey})` → `Result<Success<op>>`; session Bearer/cookie, X-Request-Id,
  `cache: no-store`, `redirect: manual`, 10 s timeout; R270 envelope / `{refusal}` / OpenAI error → `ApiError` kinds; non-JSON or network →
  `unavailable`). App port `consumerApi`, Lab port `labApi` (relative imports until the link wiring); nothing in the apps uses them (AP-09).

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| RED `pytest tests/contracts/test_openapi_export.py` before 00a/00b | 2 | collection error: `infrx.contracts.openapi` absent |
| RED same 3 cases on a copy with the base `lab_control.py`/`lab_auth.py` | 1 | 3 failed (artifact stale, baseline, lab_control documents bodies) |
| RED `pnpm --dir packages/api-client test` before the app ports | 1 | 2 failed (ports absent) |
| `pytest tests/contracts/test_openapi_export.py` | 0 | 9 passed |
| `pytest tests/contracts -m "not pg"` | 1 | 1434 passed, 1 skipped, 3 failed = `v2/test_v1_projection_pg.py` (ForeignContainer: d1 held by codex-w5-lab-rollout-4; environment, not code) |
| `pytest tests/g tests/l -m "not pg" --ignore-glob=*test_mutants*` | 1 | 865 passed, 19 skipped, 20 failed = all `*_pg.py` (same ForeignContainer refusal; no container touched) |
| `INFRX_MUTANTS=all pytest tests/g/lab_control/test_mutants.py` | 0 | 30 passed (every mutant killed, re-anchored list) |
| `python -m tests.contracts.mutants <all 536, 4 shards>` | 0 | 536/536 killed (12 new `ap00_*`) |
| `pytest tests/contracts/test_mutants.py -k "well_formed or every_case"` | 0 | green (with the openapi cases) |
| `INFRX_MUTANTS=all pytest tests/l/control/test_mutants.py` | 1 | 88 passed (83 memory mutants killed, incl. 2 re-anchored); 29 `test_pg_mutant_is_killed` NOT RUN: pristine pg baseline fails on the foreign d1 container (they mutate state/lab_control.py and lab/control, untouched here; need `INFRX_D_TASK=l3`) |
| anchor scan of every mutant list over lab_auth.py / lab_control.py | 0 | 0 broken anchors |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 458 errors (baseline 458; no new) |
| `pnpm --dir packages/api-client test` / `typecheck` / `test:mutants` | 0/0/0 | 6/6 pass; tsc clean; 10/10 transport mutants killed |
| `make console-test` / `lab-test` | 0/0 | 592 pass 59 skip 0 fail / 263 pass 14 skip 0 fail |
| `make console-typecheck` / `lab-typecheck` | 0/0 | clean (incl. the `@ts-expect-error` cross-client checks) |
| `make console-lint` / `lab-lint` | 0/0 | 0 errors (2 pre-existing App warnings) |
| `make lab-build` / `make console-built` | 0/0 | built |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

## Wiring requests

1. **Link the client into both apps** (R271: generated clients are the only API surface). `apps/app/package.json` and `apps/lab/package.json`
   dependencies gain `"@infrx/api-client": "link:../../packages/api-client"`; `pnpm install` in each app (lockfiles); in `apps/app/lib/api/index.ts`
   replace `../../../../packages/api-client/src/consumer.ts` → `@infrx/api-client/consumer` and `.../src/transport.ts` → `@infrx/api-client/transport`
   (3 lines), in `apps/lab/lib/api/index.ts` likewise with `@infrx/api-client/lab`. Composed test: `make console-typecheck lab-typecheck console-test
   lab-test lab-build console-built` + `pnpm --dir packages/api-client test` (its boundary case accepts both spellings).
2. **Makefile**: `api-client-test:` `cd packages/api-client && pnpm install --frozen-lockfile && pnpm typecheck && pnpm test`;
   `api-client-mutants:` `cd packages/api-client && pnpm test:mutants`; both in `.PHONY`; `api-client-test` in `check`. Composed test:
   `make api-client-test api-client-mutants` (exit 0; 6 tests, 10/10 mutants).

## Open items / proposals

- Proposed (for the coordinator to number): exclude `apps/infrx-api/infrx/contracts/openapi/` from `frozen_files()` like `contracts/lab/` -
  it is export tooling and a baseline that must shrink, not a wire contract; until then every baseline shrink or export edit re-pins.
- Lanes adding routes regenerate with `uv run --frozen python -m infrx.contracts.openapi.export` (artifacts + inventory) and, for 00c,
  `pnpm --dir packages/api-client generate`; a typed route leaves the baseline (and lowers the pin in the test).
- `/auth/v1/*` routes must declare their own security (public vs SessionBearer) or the export refuses them.
- The adapter reads up to 16 KiB of body before identity (was: none); nothing is parsed into a model or acted on first (ponytail comment).
- operationIds are derived (`postLabV1ControlRegister`), not hand-named.

## Estimate (remaining AP-00 00a-00c effort)

optimistic 0.5 h, likely 1 h, pessimistic 3 h; confidence medium; basis: the two wiring requests are mechanical (link + Makefile) and their
composed tests already pass on this branch; pessimistic covers a review round on the refusal adapter's pre-identity buffering or hand-named
operationIds. Spent on the lane: ~4.5 h against the brief's 5/8/14.
