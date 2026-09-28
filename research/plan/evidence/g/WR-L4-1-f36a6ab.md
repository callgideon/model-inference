# WR-L4-1 — `/lab/v1/control` + the Lab auth seam (lane lab-api, wave LW2)

- Base `f4bceeba` · code head `f36a6ab4` (task commit `1ca6a758`) · branch `codex/w5-lab-api` · worktree `.claude/worktrees/codex-w5-lab-api`
- Tasklocal key `l4` (PG 57503) for the auth seam's real half; control runs against a fake `ControlOperations` (L3 is not on the base).
- Oracles: LAB-ACCESS (cross-provider 404, consumer-only 403 on every route, revocation on the next call, no customer identity in aggregates), SPLIT-CONTRACT (records = `port.ts`, refusals = `port.ts` REFUSALS).

## Changed paths (all owned)

`apps/infrx-api/infrx/gateway/lab_auth.py` (+ its port `Sessions`), `apps/infrx-api/infrx/gateway/routes/lab_control.py` (+ its port `ControlOperations`), `apps/infrx-api/tests/g/lab_auth/{conftest,test_lab_auth,mutants,test_mutants}.py`, `apps/infrx-api/tests/g/lab_control/{conftest,test_lab_control,mutants,test_mutants}.py`.

## Design (one line each)

- `lab_auth.authenticate`: `Authorization: Bearer <compact JWT>` only (bounded regex, full match); an infrx API key / any other shape is 401 **and never forwarded**; `gateway/ingress` key auth is never consulted (audience `lab` is never a key audience).
- `GoTrueSessions`: `GET {SUPABASE_URL}/auth/v1/user` with `apikey = SUPABASE_SERVICE_ROLE_KEY` (existing secrets, **no new secret, no crypto dependency**); 200 with `aud == role == "authenticated"` and a string id = the user; 401/403 = unauthenticated; other status / transport = 503 unavailable. Signature, expiry and sign-out are the auth server's. ponytail: one round trip per Lab call; local JWKS verification is the upgrade.
- `lab_auth.member`: `LabAccess.workspaces(user)` (current, store clock, nothing cached): none = 403 (consumer-only, whatever provider named); provider not among them = 404; role without the capability (`contracts/v2 ROLE_CAPABILITIES`) = 403.
- `refusal`: 401 unauthenticated · 404 not_found · 403 denied · 422 invalid · 409 conflict · anything else 503 unavailable; body `{"refusal": reason}`; `cache-control: no-store` on records and refusals; only a non-`DomainError` is logged, by type name (never message/traceback: the token).
- `lab_control`: GET models|deployments|proposals|aggregates (read_aggregate_health), POST register + deployments/{id}/smoke (manage_dev_deployment), POST proposals (propose_publication). Actor = (session user, membership's provider and role) re-derived per call; body read (16 KiB cap, application/json, closed pydantic records: a body naming provider/user/role is 422) only after identity + capability. Aggregates = L2 `LabAccess.aggregates` (closed `DeploymentAggregate`). `operations=None` (L3 not wired) = 503 on L3's routes, health still served. Unmounted unless `rt.lab_control`.

## Red first

| cmd | exit | result |
|---|---|---|
| `uv run --frozen pytest -q tests/g/lab_auth` (tests written, no module) | 2 | collection error: `ImportError: cannot import name 'lab_auth' from 'infrx.gateway'` |
| `uv run --frozen pytest -q tests/g/lab_control` (tests written, no route) | 2 | `ImportError: cannot import name 'lab_control' from 'infrx.gateway.routes'` |
| first `INFRX_MUTANTS=all tests/g/lab_auth/test_mutants.py` | 1 | `refusal_cacheable` broken_runner (KeyError on a missing header) → assertion via `.get`; 24/24 on rerun |
| first `INFRX_MUTANTS=all tests/g/lab_control/test_mutants.py` | 1 | `actor_cached` survived (mutant only read the cache) → mutant stores it; `refusal_not_rendered` broken_runner (JSON decode of a 500) → status asserted first; 27/27 on rerun |

## Green (on `f36a6ab4`)

| cmd | exit | result |
|---|---|---|
| `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/g/lab_auth` | 0 | 12 passed (8 cases; 4 `world` cases × fake + **real PG l4**, 2+2+1 role matrix + BOTH + CONSUMER_ONLY) |
| `uv run --frozen pytest -q tests/g/lab_control` | 0 | 11 passed |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_auth/test_mutants.py` | 0 | 27 passed: 24/24 killed + 3 meta (well-formed, every case covered, pristine) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_control/test_mutants.py` | 0 | 30 passed: 27/27 killed + 3 meta |

(Final counts, `make api-test` and the wiring proof are in the LAB-API wiring evidence below, `WR-V1M-2-f36a6ab.md` § Lane checks.)

## Interface requests / wiring

- **WR-LAB-API-1 (coordinator)**: `research/plan/evidence/g/LAB-API-f36a6ab-wiring.patch` (switches `LAB_CONTROL`/`LAB_TRACES` default off, ROUTERS, `pilot._lab`, preflight NOT_SETTABLE, pins, G mutants, startup cases, Makefile api-mutants, 08 table row).
- **WR-LAB-API-2 (→ lab-access-lw2, L3)**: implement `lab_control.ControlOperations` (Actor/Registration/Model/Deployment/Proposal as defined there; typed `NotFound`/`Forbidden`/`InvalidRequest`/`Conflict`/`DependencyUnavailable`). The coordinator then composes `LabControl(sessions, access, operations=<L3>)` in `pilot._lab` (one line) and reruns `tests/g/lab_control` + the Lab's J01/J02 on `l4`.
- **WR-LAB-API-3 (→ lab-app-lw2)**: `lib/services/control/http.ts`: query `provider_org_id`, lists as `{data: [...]}`, refusals `{refusal}`; map **401 → denied** (session gone: sign in again) in addition to 404/403/422/409/5xx.

## Ruling proposal (unnumbered)

LAB-AUTH: a Lab surface of infrx-api authenticates only the Lab's forwarded Supabase access token (compact JWT), verified by the project's auth server (`/auth/v1/user`, service key as `apikey`); an API key is never a Lab credential and is never forwarded; the actor is re-derived per call from current `LabAccess` membership (consumer-only 403 on every route, foreign provider 404, missing capability 403); refusals are `port.ts`'s reasons plus 401 unauthenticated; tokens and their error text are never logged. Control reads need `read_aggregate_health`, register/smoke `manage_dev_deployment`, proposals `propose_publication`.

## Open issues

- L3 real adapter unclaimed (fake only) until L3 merges.
- No live GoTrue locally: the verifier is proven on `httpx.MockTransport`; membership on real PG.

## Estimate (remaining for WR-L4-1)

optimistic 1 h / likely 2 h / pessimistic 4 h, confidence medium. Basis: G4F 1/2/4 per route; remaining = wiring apply + L3 compose line + real-L3 rerun + one verify round.

## Audit log

- 2026-09-27: written for `f36a6ab4` (lane lab-api, LW2).
