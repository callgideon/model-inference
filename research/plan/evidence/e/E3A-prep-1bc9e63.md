# E3A-PREP — the APP-LOCAL harness, proved on the current tree (not the gate)

- **Lane:** E3A-PREP (coordinator preparation lane for task E3A). E3A's start dependency E3C is not met, so this is **not** an APP-LOCAL claim; the manifest status stays `planned`.
- **Branch / worktree:** `codex/e3a-prep`, `.claude/worktrees/codex-e3a-prep`.
- **Base:** `fd40748c` (claude/consumer-v1 with C0/U1R/A2/A3 and the G7 provisional fix).
- **Heads:** `7edad67f` (pinned `@playwright/test`), `fac6c4e1` (harness), **`1bc9e634`** (evidence runs; clean tree at start and end of every run). The evidence commit follows.
- **Label:** the App in a real browser on real PostgreSQL (every migration) / PostgREST / Valkey / S3-compatible store / gateway and worker processes on the runtime login, with an auth stand-in and E2's controlled engine. Not Marlin quality, GPU capacity, hosted behaviour or real email.

## Changed paths

| Path | What |
|---|---|
| `apps/app/package.json`, `apps/app/pnpm-lock.yaml` | `@playwright/test` **1.63.0** (exact, dev). `pnpm install --frozen-lockfile` passes. |
| `tests/integration/app/runner.py` | The APP-LOCAL runner: composition, control API, App build/start, browser run, verdict, teardown, `--only`, `--break-seam`. |
| `tests/integration/app/edge.py` | The App's Supabase origin: auth stand-in (`/auth/v1/*`) and a PostgREST proxy (`/rest/v1/*`), with a `freeze` mode for the fixture-port seam. |
| `tests/integration/app/test_e3a_runner.py` | 11 unit tests (no stack): cells equal the manifest's test_ids, skipped or absent is never PASS, seam detection, loopback-only env, edge tokens and one-use links, frozen reads only, gate wiring. |
| `apps/app/tests/e2e/journey.e2e.ts` | 19 browser checks (one Playwright test each). |
| `apps/app/tests/e2e/playwright.config.ts`, `target.ts`, `target.test.ts` | Config. It refuses to run without the runner's loopback URLs; `target.test.ts` (node --test) proves the refusal. |
| `tests/integration/gates.py` | Smallest additive change. `e3c_stage` takes `name`/`sub` (defaults unchanged), and the app-e2e gate's `browser-journey` stage runs `tests/integration/app/runner.py` instead of a fixed NOT RUN. |

## Composition

Reading top to bottom:

1. The runner takes a host lock (`/tmp/infrx-e4b.app-runner.lock`) and runs preflight: node_modules present, a browser under `PLAYWRIGHT_BROWSERS_PATH`, no `apps/app/.env*` other than the example, and its own three ports free.
2. It builds the App (`next build`) with only local values. `local_env` refuses any URL that is not `http://127.0.0.1:<e4b port>` and any hosted name.
3. It loads **E3C's runner and world** by file and reuses them, rebuilding nothing. `e3c.provision` runs E2's preflight/services/migrate in namespace `e4b`. `world.composed(runtime_login=True, MAX_ACTIVE_JOBS_PER_KEY=2)` provides a fresh migrated clone, two operator-provisioned tenants, the journey PostgREST over the clone, E2's fake engine as its own process, and the **gateway and worker as their own process groups on the dedicated `infrx_runtime` login**.
4. On that clone the runner replaces `auth.uid()` with the hosted form (see deviations). The **edge** (`edge.py`) serves 127.0.0.1:56860 as the App's `NEXT_PUBLIC_SUPABASE_URL`:
   - A signup inserts `auth.users` on the clone, and the 0001 trigger creates the profile and personal organization.
   - Verification sets `email_confirmed_at`.
   - Tokens are HS256 under the journey PostgREST's local secret, so RLS sees the real user.
   - `/rest/v1/*` is proxied to the journey PostgREST.
5. A **control API** (127.0.0.1:56861, never the App's origin) gives the specs the harness side:
   - the mailbox and database facts (wallet, grants, jobs, holds, debits, keys, and the `conserved` oracle reused from `pilotbox.Journey`);
   - the operator CLI (`issue-key`, `adjust`);
   - the engine fault, worker stop/start, the test clock and the auth token TTL.
6. **App:** `next start -H 127.0.0.1 -p 56870` in its own process group. The environment contains only `NEXT_PUBLIC_SUPABASE_URL`, the anon key, a service-role JWT that only this PostgREST accepts, `INFRX_API_BASE_URL` pointing to the local gateway, and a random `CONSOLE_CURSOR_SECRET`.
7. **Browser:** Playwright runs the headless Chromium shell from the scratchpad with one worker. It browses `http://localhost:56870` (see deviations). The external client is Node `fetch` to the gateway using the individual's key.
8. **Verdict:** each test title is `<check>: …`. Checks map to the 17 E3A test_ids, and a cell is the worst of its checks. The gate is the worst of the cells and the stages.
9. **Teardown:** the App, the edge, the box, PostgREST and the engine are stopped, then E2's stack. `docker ps -a` shows no `infrx-e4b*`, checked by the runner and recorded as a stage.

## Ports

| Port | What |
|---|---|
| 56832 | PostgreSQL |
| 56879 | Valkey |
| 56800 | S3 |
| 56823 / 56890 | ClickHouse |
| 56880 | Fake engine |
| 56831 | Journey PostgREST |
| 56840 | Gateway |
| 56860 | Edge |
| 56861 | Control API |
| 56870 | App |

All of these are E2's `e4b` layout (+1300) or this lane's additions, bound on 127.0.0.1. Two items fall outside that rule:

- The worker's health port is an OS-assigned loopback port (`pilotbox.free_port`, inherited from E3B/E3C).
- `55432` and the `e3c` block (56900–56999) were never touched.

## Verdict: full run at `1bc9e634`

Gate **FAIL** (exit 1). Wall time 89 s: build 8 s (incremental), stack 10 s, world 6 s, App 1 s, browser 39 s, teardown 4 s. A cold first build took 49 s. Playwright: 13 passed, 2 failed, 4 skipped (NOT RUN).

| Cell | Verdict | Why |
|---|---|---|
| DUR-ADMIT | NOT RUN | `revoke-key` NOT RUN (C3A, U2); text-sync, rate-rejection and low-funds PASS |
| DUR-CAP | PASS | rate-rejection |
| DUR-FENCE | PASS | rate-rejection (queued jobs settle once after a worker replacement) — weak for fencing; E3C s05 is the real proof |
| DUR-OUTPUT | NOT RUN | `request-detail` NOT RUN (U4); sse, async and expired-result PASS |
| DUR-SETTLE | PASS | text-sync, retry, usage-balance, accounting-uncertainty |
| DUR-OUTBOX | PASS | async-poll, rate-rejection |
| DUR-RLS | **FAIL** | `provider-route-denial` (F-2); isolation PASS |
| MEDIA-SEC | PASS | video-upload, isolation (a foreign upload ref is refused 4xx, nothing admitted) |
| API-MODES | PASS | text sync, async poll, uploaded video |
| API-STREAM | PASS | SSE identity/deltas/usage/[DONE] |
| CONSOLE-FLOWS | **FAIL** | `signin-claim` (F-1) and `provider-route-denial` (F-2) FAIL; create-key, request-detail, operator-controls and revoke-key NOT RUN |
| CREDIT-GRANT | PASS | verify → 10,000 once; replayed link refused; a second callback (recovery) claims again → still 1 grant; user B its own 1 |
| CREDIT-IDENTITY | PASS | an unverified user cannot sign in (nothing granted); tenants separate |
| CREDIT-UNITS | PASS | exact decimals shown equal the ledger; CREDIT labelled; no `$` figure |
| CREDIT-RATE | PASS | `conserved`: every charge = admitted card × usage, ledger = grant − charges |
| CREDIT-SPEND | PASS | usage rows and figures, retry not charged twice, `held_unknown` shown as "Awaiting reconciliation" with the exact hold and no charge, 402 at low funds with nothing admitted |
| APP-JOURNEY | **FAIL** | every check |

Per check:

| Check | Verdict | Notes |
|---|---|---|
| signup-verify-grant | PASS | |
| signin-claim | **FAIL** | 2 claims, expected 3 |
| create-key | NOT RUN[C3A,U2] | |
| text-sync | PASS | |
| sse-stream | PASS | |
| async-poll | PASS | |
| video-upload | PASS | |
| retry | PASS | |
| usage-balance | PASS | |
| refresh | PASS | a reload after an access token inside auth-js's 90 s margin; the refresh-token grant counted |
| request-detail | NOT RUN[U4] | |
| accounting-uncertainty | PASS | engine `missing_usage` |
| rate-rejection | PASS | cap 2, third request 429 `capacity_exhausted` + Retry-After, nothing admitted |
| low-funds | PASS | |
| provider-route-denial | **FAIL** | |
| operator-controls | NOT RUN[U3] | |
| isolation | PASS | |
| revoke-key | NOT RUN[C3A,U2] | |
| expired-result | PASS | 410 `result_expired` after the clock passes the persisted expiry; metadata kept |

NOT RUN comes from the manifest (`tasks.json` status `implemented`/`integrated`). A check whose lane is not merged skips `NOT RUN[<lane>]`, and the runner reports it and never counts it as a pass. A check that finds no earlier state skips `NOT RUN[journey]`.

### Product findings (the two FAILs)

- **F-1 — the first-login grant claim never runs** (A2 path, middleware):
  - Mechanism: `login-form.tsx` calls the server action `claimOnboarding` after `signInWithPassword`. The browser client has already set the session cookies, so `lib/supabase/middleware.ts` redirects the action's `POST /login` to `/models` (observed: `307`) and the action never executes. `afterSignIn` swallows the failure and lands on `/welcome`.
  - Impact: the claim at sign-in is dead code. A verified user whose callback claim failed is only rescued by the `/welcome` retry. A replayed (onboarded) user always lands on `/welcome` instead of `next`.
  - Suggested fix (not applied; not an owned path): redirect only non-POST requests to `/login` and `/signup` in `updateSession`, or exempt `Next-Action` requests.
  - Oracle: `signin-claim` (claim RPC count +1 on sign-in).
- **F-2 — provider routes are served to a consumer:**
  - `/traces` (200, data-unavailable panel), `/dedicated` (200, dedicated-hosting sales page) and `/teams` (200, the organization's member table) all render for a signed-in consumer. The sidebar hides them. `/admin` is refused.
  - Brief 04 ("Shared consumer boundary"): keep these "out of consumer navigation and protected routes. Do not merely hide a link to an unguarded provider route."
  - Owner: coordinator (shared layout/routes).

## Break seams (failure oracle), at `1bc9e634`

| Seam | What is broken | Checks | Result | Wall |
|---|---|---|---|---|
| `fixture-port` | The edge answers each App read (`GET`, `rpc/console_*`, `rpc/consumer_*`) with the first answer it ever got (a recorded fixture); writes and the grant are untouched | signup-verify-grant PASS, text-sync PASS, **usage-balance FAIL**: displayed Available `10000` vs ledger `9999.4592` | gate **FAIL** (exit 1), `seam:fixture-port` FAIL "broken seam detected" | 36 s |
| `grant-guard` | E3B db09 on the clone (entitlement key, one-grant index, replay answer, once guard) | **signup-verify-grant FAIL**: `[entitlements, grant rows, total] = [2, 2, "20000"]` after the recovery callback | gate **FAIL** (exit 1), `seam:grant-guard` FAIL | 31 s |

**Failed-then-fixed during development (the oracle was too weak):**

- The first grant-guard run survived: `seam:grant-guard` PASS, "BROKEN SEAM NOT DETECTED". The only repeated claim in the journey was the sign-in claim, which never reaches the database (F-1).
- The journey now also replays the callback through a second email link (password recovery). The App's callback claims for every verified link, and that is what turns the seam red.
- The first fixture-port run failed on a count guard (`jobs >= 4`, with only 3 checks selected) rather than on the display. The guard is now `>= 1`, and it now fails on the displayed balance.

## Deviations, each with its reason

1. **`auth.uid()` replaced on the clone** with the hosted form, which reads `request.jwt.claims`. The pinned supabase image's version reads only `request.jwt.claim.sub`, which PostgREST v13 does not set. Without the replacement every signed-in App read saw no user: the /welcome wallet showed "not issued" while the ledger held the grant (run 3). C0 (`apps/app/tests/c/realdb/stack.py`) and D10 (`tests/d/test_postgrest_d10.py`) apply the same replacement. E3A-WR-2 moves it into E3C's template.
   - **Risk for E3C, not verified:** any E3C case that reads through PostgREST as `authenticated` may see `auth.uid()` null and pass deny checks vacuously. For example, s10's browser-role reads may be affected, unless s10 sets the legacy GUC itself.
2. **The browser uses `localhost`, not 127.0.0.1.** Next's `nextUrl` rewrites every loopback host to `localhost`, so the email callback's redirect lands on `localhost:56870`, and the cookies set on 127.0.0.1 were lost (run 2). The server still binds 127.0.0.1. `target.ts` accepts exactly `127.0.0.1` or `localhost` on an e4b port.
3. **`world.NAMESPACE` is set to `e4b` at load time** (a ponytail shim). `need_stack()` compares against the constant `"e3c"`, while the harness already follows `INFRX_E2_NAMESPACE`. E3A-WR-1 removes the shim.
4. **The key for external calls comes from the operator CLI** (`issue-key`), because C3A/U2 have not merged. It is labelled `keySource` in the journey state, and `create-key`/`revoke-key` stay NOT RUN.
5. **The email is a mailbox** on the control API. A real verified email is E4's.

## Commands

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env |
| `cd apps/app && pnpm install --frozen-lockfile` (base) | 0 | |
| `pnpm add -D --save-exact @playwright/test@1.63.0` | 0 | +2 packages |
| `PLAYWRIGHT_BROWSERS_PATH=<scratchpad>/ms-playwright pnpm exec playwright install chromium-headless-shell` | 0 | chromium_headless_shell-1243 in the scratchpad only |
| `pnpm install --frozen-lockfile` (after the dependency commit) | 0 | |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/app tests/integration/backend/e3c/test_e3c_runner.py tests/integration/test_preflight.py` | 0 | **103 passed** (11 E3A + 34 E3C + 58 gates/preflight) |
| `make console-test` | 0 | 510 tests, 487 pass, 0 fail, 23 skipped (the pre-existing env-gated real-DB cases: U1R_PG_DSN, the C0 stack, INFRX_APP_A2_DSN) |
| `make console-lint` / `make console-typecheck` | 0 / 0 | |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |
| `PLAYWRIGHT_BROWSERS_PATH=… apps/infrx-api/.venv/bin/python tests/integration/app/runner.py --out $SP/final-full` | 1 | FAIL (above), 89 s |
| `… runner.py --out $SP/final-fixture-port --break-seam fixture-port` | 1 | FAIL (detected), 36 s |
| `… runner.py --out $SP/final-grant-guard --break-seam grant-guard` | 1 | FAIL (detected), 31 s |
| `docker ps -a --format '{{.Names}}' \| grep -c infrx-e4b` (after each run) | — | 0 |

Development runs 1–5 and the debug run happened before `fac6c4e1`: they exposed the alert selector, deviation 2, deviation 1, F-1, and the seam oracles above. The raw logs of the three evidence runs are in [`E3A-prep-1bc9e63/`](E3A-prep-1bc9e63/), one directory per run, with these files:

- `verdict.json`, `runner.log`, `browser.log` and `playwright.json`;
- the App's `app.log` and `app-build.log`;
- the box's gateway, worker and calls logs.

There is no media: no screenshots, no video, and the processing cache's clip is left out. There are no secrets: the logs were scanned for keys, JWTs, DSNs and the local password.

## Wiring requests (not applied)

- **E3A-WR-1** (`tests/integration/backend/e3c/world.py`, E3C): `need_stack()` should accept the namespace the harness loaded, for example `if harness.NAMESPACE != os.environ.get("INFRX_E2_NAMESPACE", NAMESPACE)`. Then `runner.py` drops `world.NAMESPACE = NAMESPACE`. Test: the E3A full run passes its `world` stage without the shim.
- **E3A-WR-2** (`tests/integration/backend/stack.py::_template`, E3B/E3C): apply the hosted `auth.uid()` (`runner.HOSTED_AUTH_UID`, run as `supabase_admin` like the `auth.users` columns there). Then `runner.py` drops its per-clone step. Test: an E3C case reading `consumer_jobs` through the journey PostgREST as a tenant's JWT returns that tenant's rows. Also re-check s10 for vacuous deny cases.
- **E3A-WR-3** (`tests/integration/ENVIRONMENT.md` and `environment.json`/`preflight.py` app-e2e profile, E2C): declare the browser prerequisite:
  - `PLAYWRIGHT_BROWSERS_PATH`;
  - `pnpm exec playwright install chromium-headless-shell` into a scratch directory;
  - that the app-e2e gate now ends with the browser journey, which needs Docker and the e4b block.
- **E3A-WR-4** (`apps/infrx-api/infrx/contracts/tasklocal.py`, coordinator), optional: record that E3A uses 56860/56861/56870 inside `e4b`, or give E3A a block of its own (`harness.NAMESPACES` row + `TASK_BLOCKS`) if E4B and E3A must run at the same time. Today the runner's port preflight and lock refuse a busy block.
- **F-1** (A2 / owner of `lib/supabase/middleware.ts`) and **F-2** (coordinator, shared routes): see the product findings above.

## What E3A proper still needs

1. **E3C BACKEND-LOCAL passing** (E3A's start dependency). Then rerun this runner on the merged SHA. E3C's evidence covers the backend matrix and is not repeated here.
2. **C3A + U2 merged:** `create-key` and `revoke-key` run. Their selectors are written against today's `/api-keys` dialog and must be fitted to U2's page (it may move to `/keys`). The runner then uses the App-created key instead of the CLI's.
3. **U4 merged:** `request-detail` runs. Fit the selectors to U4's page, and add the expired-content display to `expired-result` (the API half runs today).
4. **U3 merged:** `operator-controls` runs. It needs an operator identity, which the control API can mark through `profiles.is_operator`, plus the approved action UI.
5. **F-1 and F-2 fixed or ruled.**
6. **WR-1 and WR-2 applied** (the shims removed), and WR-3 documented.
7. Optional: an App-level worker SIGKILL mid-job, if the coordinator wants DUR-FENCE carried by E3A rather than by E3C s05.

## Estimate (E3A proper, after its dependencies merge)

| Optimistic | Likely | Pessimistic | Confidence |
|---|---|---|---|
| 3 h | 6 h | 12 h | medium |

Basis:

- The harness composes, runs in about 1.5 min, and has both seams red.
- The remaining work is fitting selectors for four checks to UIs that do not exist yet (C3A/U2/U3/U4), applying two wiring requests, and one or two merged-SHA reruns.
- The pessimistic case assumes U2/U4 change routes or flows substantially, or F-1/F-2 need App changes before the gate can be green.

## Audit log

- 2026-09-26: Created by the E3A-PREP lane (Opus implementer) at `1bc9e634`. The manifest's E3A status is unchanged (`planned`).

## Fix round (2026-09-26): review E3A-PREP-R1/R2/R3

- **Handback reviewed:** `e315a5ee`. **Code head:** `31cf6f10`, from three commits:
  - `33b5a027`: runner fixes;
  - `5285d88e`: spec fixes;
  - `31cf6f10`: journey order.
  The evidence commit follows. Base unchanged (`fd40748c`).
- **Changed paths:**
  - `tests/integration/app/runner.py`
  - `tests/integration/app/test_e3a_runner.py`
  - `apps/app/tests/e2e/journey.e2e.ts`
  - this section, and raw logs in [`E3A-prep-1bc9e63/fix-31cf6f1/`](E3A-prep-1bc9e63/fix-31cf6f1/)
  No dependency, gate or wiring change. `pnpm install --frozen-lockfile` still passes.

| Finding | Fixed | How |
|---|---|---|
| 0-E3A-PREP-R1 / 1-E3A-PREP-R1 (blocking): DUR-FENCE, DUR-OUTBOX, CREDIT-RATE and DUR-CAP PASS unexercised | yes, option (a) | New `DELEGATED` map in `runner.py`. Each cell now records `journey` (the worst of its checks, the journey slice) apart from `verdict` (the oracle). For these four cells, `verdict = worst(journey, NOT RUN)`: they are never PASS, but they still FAIL when a check under them fails. The first reason reads `NOT RUN[delegated] <what the oracle needs>: <what the journey does>; <carrying cases>; rerun on the merged SHA (journey slice …)`. The carrying cases are E3C s05/s08, E3B dr03/dr05/db07/db11/dr13/db10/dr07c/db13 and dr02c/dr12/s09. For DUR-CAP it also records that no case races admissions across keys. |
| 0-E3A-PREP-R2 (major): operator-controls PASS on the U3 flag alone | yes | The check skips `NOT RUN[operator-action]` whatever the manifest says. The vacuous body is removed; a consumer's `/admin` refusal stays under `provider-route-denial`. |
| 0-E3A-PREP-R3 (major): revoke-key can never pass (the `confirm()` dialog was dismissed) | yes | Adds `page.once("dialog", accept)` before the click. The click targets `Revoke <name>` (exact) for the key create-key made; its name is kept in the journey state as `appKeyName`. |
| 1-E3A-PREP-R2 (major): INVALID after I2A (`next start` refuses without an environment) | yes | New `app_env()` states `INFRX_APP_ENVIRONMENT=development`, the environment whose loopback origins `assertDeployEnv` accepts. A unit test holds it. |
| 1-E3A-PREP-R3 (major): a failed `next start` leaves its process group holding 56870 | yes | `NextApp.__enter__` calls `__exit__` before re-raising. `__exit__` sends SIGTERM, then SIGKILL, to the whole group until no member is left, even when the leader has already exited. `leftovers()` and the teardown stage now also fail on a held edge, control or App port. |

**New defect found by the fix, and fixed (`31cf6f10`):**

- Once revoke-key worked (probe P2), `expired-result` ran after it with the now-revoked App key. It got a 401 instead of its result, so DUR-OUTPUT went FAIL.
- `expired-result` now runs before `revoke-key`. The journey ends with the revocation, which is the order the brief gives.

### Tests first (failed before, pass after)

| Test (`tests/integration/app/test_e3a_runner.py`) | Before (runner at `e315a5ee`, names refactored only) | After |
|---|---|---|
| `test_all_green_is_pass_and_absent_or_skipped_is_never_pass` (changed) | `KeyError: 'journey'` | pass |
| `test_cells_the_journey_does_not_exercise_are_never_pass` (R1) | no `DELEGATED` (4 cells PASS) | pass |
| `test_the_app_env_states_its_environment` (1-R2) | `KeyError: 'INFRX_APP_ENVIRONMENT'` | pass |
| `test_an_app_that_never_answers_leaves_no_process` (1-R3) | `'sleep 60 & exec sleep 60': its process group outlived the failed start` | pass (both the timeout and the leader-exited cases) |
| `test_teardown_counts_a_held_port_as_left_behind` (1-R3) | `leftovers()` ignored ports | pass |

Browser-level failed-then-passed. Each probe is `consumer-v1` `6badd4e1` (C3A/U2/U3/U4/A2 implemented, I2A instrumentation present) merged into this branch. The merges were made in a `git clone --shared` in the scratchpad, never pushed, with their own `make api-env` and `pnpm install --frozen-lockfile`.

| Probe | Merge of | Result |
|---|---|---|
| P1 | `33b5a027` (runner fixed, old spec) | Every stage PASS: the App serves under I2A's instrumentation (1-R2). `operator-controls` **PASS** with no operator action (0-R2 reproduced). `revoke-key` **FAIL**: "Expected: true, Received: false" after the 20 s poll (0-R3 reproduced). 16 passed, 3 failed. |
| P2 | `5285d88e` (spec fixed) | `revoke-key` **PASS**; `operator-controls` NOT RUN[operator-action]. `expired-result` FAIL 401: the ordering defect above. |
| P3 | `31cf6f10` (code head) | Gate FAIL. **16 passed, 2 failed, 1 NOT RUN**. The 2 failures are the product findings F-1 (`signin-claim`) and F-2 (`provider-route-denial`), both still open on consumer-v1. Cells: 10 PASS, 4 NOT RUN (delegated, journey PASS), 3 FAIL (DUR-RLS, CONSOLE-FLOWS, APP-JOURNEY). Teardown PASS: no container, and ports 56860, 56861 and 56870 free. Took 94 s. |

On consumer-v1, `create-key` (U2's dialog) and `request-detail` (U4's page) already pass with the existing selectors.

### Runs at `31cf6f10` (this branch, clean tree at start and end)

| Run | Exit | Result |
|---|---|---|
| full | 1 | Gate FAIL, 54 s. Playwright: 13 passed, 2 failed (F-1, F-2), 4 skipped (C3A/U2/U4 not merged at the base; operator-action). Cells: **8 PASS, 6 NOT RUN, 3 FAIL**. The 6 NOT RUN are DUR-ADMIT and DUR-OUTPUT (lanes), plus DUR-CAP, DUR-FENCE, DUR-OUTBOX and CREDIT-RATE, which are delegated with a PASS journey slice. At `1bc9e634` the result was 12 PASS / 2 NOT RUN / 3 FAIL; the difference is the four delegated cells that had passed without being exercised. |
| `--break-seam fixture-port` | 1 | `seam:fixture-port` FAIL, detected (`usage-balance`), 29 s. |
| `--break-seam grant-guard` | 1 | `seam:grant-guard` FAIL, detected (`signup-verify-grant`), 35 s. |

After each run, `docker ps -a | grep -c infrx-e4b` = 0. No App process was left: the teardown stage checks the ports.

### Commands

| Command | Exit | Result |
|---|---|---|
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/app` (new tests, old behaviour) | 1 | 5 failed, 10 passed (`fix-31cf6f1/unit-before.log`) |
| `… -m pytest -q tests/integration/app tests/integration/backend/e3c/test_e3c_runner.py tests/integration/test_preflight.py` | 0 | **107 passed**: 15 E3A + 34 E3C + 58 gates/preflight (`unit-after.log`) |
| `make console-test` | 0 | 510 tests, 487 pass, 0 fail, 23 skipped (the pre-existing env-gated real-DB cases) |
| `make console-lint` / `make console-typecheck` | 0 / 0 | The 2 pre-existing warnings in `lib/contracts/` |
| `cd apps/app && pnpm install --frozen-lockfile` | 0 | |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |
| `PLAYWRIGHT_BROWSERS_PATH=… runner.py --out …` (full, and each seam), at `31cf6f10` | 1 / 1 / 1 | above |
| probes P1–P3 (the merged clone's own `runner.py`, same block, run one after another) | 1 / 1 / 1 | above |

Raw logs are in `fix-31cf6f1/{full,fixture-port,grant-guard,probe-p1,probe-p2,probe-p3}/`: `verdict.json`, `runner.log`, `browser.log`, `playwright.json` and `app.log`. They were scanned for key prefixes, JWTs, DSNs with passwords and the local password: none found.

### Open after this round

- F-1 and F-2 still FAIL on consumer-v1 `6badd4e1`, which is the same result as at `1bc9e634`.
- The four delegated cells keep APP-LOCAL at NOT RUN or worse until E3A proper does one of two things:
  - (a) import E3C's same-SHA verdict (s05, s08) and the E3B drill results. DUR-CAP's cross-key admission race has **no carrying case** today.
  - (b) add journey injections: a worker SIGKILL mid-lease, a Valkey flush, a card published while jobs are queued (`publish-card` exists in the operator CLI), and concurrent admissions across two keys or orgs.
- `operator-controls` needs the operator action itself: an operator identity on the edge, a U3 action with a reason, and one audit row after a repeat.
- The E3A-WR-1…4 wiring requests are unchanged.

**Estimate for E3A proper**, after its dependencies:

| Optimistic | Likely | Pessimistic | Confidence |
|---|---|---|---|
| 3 h | 6 h | 12 h | medium |

The basis has changed: create-key, request-detail and revoke-key already pass on consumer-v1, so the remaining work is:

- the operator action;
- resolving the delegated cells (import or injections, which carries the most variance);
- F-1 and F-2;
- WR-1 and WR-2;
- merged-SHA reruns of about 1.5 min each.

### Audit log

- 2026-09-26: Fix round appended by the E3A-PREP fix lane (Opus implementer) at code head `31cf6f10`. Earlier sections are unchanged. The manifest's E3A status is unchanged (`planned`).
