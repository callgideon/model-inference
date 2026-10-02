# api-frontends-lab — AP-09 09c/09e (the Lab) — evidence at cd1fca2

Lane `codex/w7-api-frontends-lab`, base `b05eb6f4`, code head `cd1fca28`. Commits: `f0e6fe47` (slice 1: Lab shell over the
API), `6d4eb659` (slice 2: judge + review over the API), `825d5d4d` (slice 3: API-BOUNDARY), `cd1fca28` (V1M runner fix).

## Done
- **09c shell.** `lib/auth/*`: sign-in / sign-out / email-link callback / proxy refresh through the auth facade `/auth/v1/*`
  on `LAB_API_URL`; the session is the facade's tokens in two http-only, host-only cookies (`infrx-lab-session`,
  `infrx-lab-refresh`); the proxy refreshes within 60 s of `exp` (401 clears, 429/503/network keeps). Memberships from
  `GET /lab/v1/workspaces` as the session (401 = signed-out, any other failure = unavailable, any bad row fails closed, an
  unknown capability is dropped); `workspaceFeatures()` reads `GET /lab/v1/capabilities` (failure = `{ok:false}`, never
  disabled). `holds()` answers from a workspace's API capability set (the role table only for bare-role presentation
  callers); `land()` uses the workspace's set. No `@supabase` import remains in any production module.
- **Register row 76.** `labApiUrl(env)` = `LAB_API_URL` only; the six per-family names and `NEXT_PUBLIC_SUPABASE_*` are
  read nowhere (L1-C06 scans lib/app/components); the six family `server.ts` adapters use `config.apiUrl`.
  `.env.example`/README updated. `vercel env rm` of the six names + the two Supabase vars is the coordinator's.
- **09c judge/review.** `judge/core.ts` over `/lab/v1/judge/{configs,budgets/{payer_ref},runs,calibration}` (keys: the form's
  render-minted `run_id` for runs, a form `idempotency_key` for configs/budgets; 401/403/404 denied, 409 conflict,
  422 invalid, else unavailable); `review/index.ts` reads `/lab/v1/traces/{id}/feedback` (closed signal fields + human
  reviews) and adds `submitReview` (`POST .../reviews`, form key, provenance the server's) + action `submitRequestReview`.
  The per-request judge read (`judgeRunsPort`) is honestly unavailable (no API route: WR-AP09L-3). `judge/session.ts` deleted.
- **09e boundary.** `tests/boundary/api.test.ts` (L1-T01 no RPC/table/Supabase client/setting; T02 every `.call` is in
  `lab-control.json` or ShellPaths; T03 ShellPaths pinned to `consumer.json`), run by L1's runner (no Makefile change).
- Not done: 09d (ux-lab-operate). The judge page forms (ux-lab-evaluations) still send no `idempotency_key` for
  configure/budget and no `config_id` for calibration: those three actions answer `invalid` until that lane adds the fields.

## Red runs (tests first)
- Slice 1: `node --test tests/l/shell/*.test.ts` against the old modules: 7 failing (4 files fail to load, G01-G03 fail).
- Slice 2: `node --test tests/c/judge/*.test.ts tests/c/review/*.test.ts`: 4 failing (3 files fail to load, C3P-04).
- Slice 3: `tests/boundary/api.test.ts` on a `git archive b05eb6f4` copy: 3/3 fail (T01 lists config.ts SUPABASE,
  guard.ts RPC, ...).

## Checks (exit codes)
| command | exit | result |
|---|---|---|
| `make lab-test` | 0 | 286 tests: 276 pass, 0 fail, 10 skipped |
| `make lab-lint` | 0 | clean |
| `make lab-typecheck` | 0 with WR-AP09L-1 applied; **6 TS errors without it** | all six: judge/review paths absent from the stale `packages/api-client/src/lab.ts` |
| `make lab-build` | 0 (WR-AP09L-1 applied) | |
| `make api-client-test` | 0 with WR-AP09L-1 (red at base: stale generated clients) | 6/6 |
| `make lab-mutants` (runners in Makefile order) | 0 after cd1fca2 | l/shell 48 cases 134/134; l/ui 21 86/86; c/review 8 43/43; v/detail 11 31/31; v/judge 8 36/36; v/list 16 71/71; c/judge 16 58/58; r 32 128/128; p 45 219/219; b 34 157/157; n 27 43/43; e2e 4 17/17; l/shared 3 9/9; ux 9 16/16 — 0 survivors, every case named |
| `make api-lint` / `make api-typecheck` | 0 / 0 | no Python committed; with WR-AP09L-2 applied: ruff clean, pyright 458 (baseline 458) |
| WR-AP09L-2 composed test + `pytest -k "not pg" tests/contracts tests/l/control/test_operations.py tests/g/test_startup.py` | 0 | 1465 + 70 passed (WR applied, then reverted) |
| `make lab-e2e` (l4) | not run: **BLOCKED** | the built Lab signs in through `/auth/v1/*` and reads `/lab/v1/workspaces` on `LAB_API_URL`; the e2e stack's Lab unit mounts neither until WR-AP09L-2 and api-identity-2's Lab-unit mount (SessionActors + lab_workspaces, SR-AP01-1) land; the review page also needs `rt.actors` on the unit for LAB_JUDGE_API |

## Wiring requests (coordinator)
- **WR-AP09L-1** `cd packages/api-client && pnpm generate` (both clients stale at base; after WR-AP09L-2 so `lab.ts`
  carries `/auth/v1/*`). Composed test: `make api-client-test lab-typecheck lab-build`.
- **WR-AP09L-2** the Lab unit mounts the auth facade behind `AUTH_FACADE` (default off) on its own publishable key +
  `WEB_ORIGINS`; export documents it; inventory rows move to `api`. Patch: `api-frontends-lab-cd1fca2-WR-AP09L-2.patch`
  (`infrx/lab/control/app.py` `_auth_facade` + `auth.register`, `export.py` `"AUTH_FACADE": "1"`, `inventory.py`
  Lab rows, regenerated `lab-control.json` + route inventory). Composed test (run, passed; to add as
  `tests/l/control/test_lab_auth_facade.py`): `create_app()` with the three INFRX_LAB_* lists no `/auth/v1/*` (404);
  with `AUTH_FACADE=1 WEB_ORIGINS=https://lab.example` lists sign-in/refresh/sign-out/callback, sign-in answers 503
  `unavailable` (IdP down) and 403 for a foreign Origin. Note: the facade module also mounts sign-up/recovery/password.
- **WR-AP09L-3** (api-judge-2) a per-request judge read for the request page: `GET /lab/v1/traces/{request_id}/judge-runs?provider_org_id=`
  → `ListPage[RunDoc + results]` over 0043's `lab_judge_runs`; then `lib/services/judge/runs.ts` maps it.
- **WR-AP09L-4** (api-frontends-app / coordinator) `apps/app/tests/c/feedback/stack.py:451-453`: drop the two `LAB`
  entries (`review-postgrest`, `feedback-postgrest`, deleted: the Lab no longer reaches PostgREST) and the docstring line 17.
- **WR-AP09L-5** `apps/lab/package.json` + lockfile: `pnpm remove @supabase/ssr @supabase/supabase-js` (no importer left).
- Deploy order: this Lab build needs the Lab unit with `AUTH_FACADE`, the identity mount and `LAB_JUDGE_API` + actors on,
  and `LAB_API_URL` set in Vercel, before it ships (batch-3 cutover).

## Deviations (outside owned paths, each the direct consequence of the brief)
Family `server.ts`/`port.ts` comments + `config.apiUrl` (6 families), family wiring/actions tests and their runners
(b, n, p, r, v/list, l/ui: new seams; equivalent per-family-name mutants deleted), `components/traces/judge/port.ts`
(2-line seam to `judgeRunsPort`), `tests/c/**`, `tests/v/{detail,judge}`, `tests/e2e/harness.ts` (env),
`tests/ux/harness/next.config.ts` (Turbopack root must hold the linked client), `apps/lab/{README.md,.env.example}`.

## Proposed rulings
- The Lab's session is the auth facade's tokens in Lab-owned http-only cookies; the Lab holds no IdP URL or key.
- A capability unknown to a web client is dropped, not trusted and not a read failure.

## Estimate (remaining AP-09 Lab, incl. merge/wiring)
optimistic 2 h / likely 4 h / pessimistic 8 h, confidence medium. Basis: lane work done (~9 h of 6/10/16); remaining is
applying WR-AP09L-1/2/5, re-running lab-e2e once api-identity-2's unit mount lands (expect stack.py stand-ins to need no
change: the facade calls the stand-in's `/auth/v1/token`), and WR-AP09L-3.
