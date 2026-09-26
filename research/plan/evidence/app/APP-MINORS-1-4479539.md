# APP-MINORS-1 — carried App minors (R-1, I3R-5/6/8, shared limiter) — evidence

- Lane APP-MINORS-1, branch `codex/app-minors-1`, worktree `.claude/worktrees/codex-app-minors-1`.
- Base `9b21339a`. Code head `4479539d` (commits `99a813a7` R-1, `9f842af5` I3R-6, `4479539d`
  mutant re-anchors); this file and `updates/APP-MINORS-1-20260926T0724Z.json` are committed on top.
- Nothing hosted, Vercel, box, AWS or SSM was touched. Docker only as `app-u1r` (55457) and
  `app-c0` (55451); both containers removed by the harness on exit. Contracts untouched.

## Items

| Item | What it was (source) | Where | Disposition |
|---|---|---|---|
| R-1 | APP-0024-WIRE-2 lens minor: U1R-P08 asserted `next_cursor === null` after exactly one follow-up page, so it held only for 101–199 rows | `apps/app/tests/u/credit-pg.test.ts:395` | **Fixed** (99a813a7) |
| I3R-6 | I3-PREP lens: the nearer segment boundaries `app/(console)/{usage,billing,traces}/error.tsx` catch first, render their copy and never POST `/api/client-errors`, so browser throws on those routes produce no `app_error` line | the three `error.tsx` | **Fixed** (9f842af5) |
| I3R-5 | I3-PREP lens: `infra/app/rollback.py:102` compares `(MAJOR, MINOR)` tuples, so an App on contracts-v2.1 passes against a gateway on v3.0 (latent: both read v2.1 today). Not an `/api/version` consumer and not in `infra/alerts` | `infra/app/rollback.py` + `tests/integration/ops/test_i3_rollback.py` | **Not owned → WR-AM1-1** (exact patch, fails-before/passes-after verified in a clone) |
| I3R-8 | I3-PREP lens: prose, not a test — `infra/app/operations.md:160` states one unobserved Vercel state as fact ("a log drain is optional and not configured [OP]") | `infra/app/operations.md` | **Not owned → WR-AM1-2** (reword) |
| shared per-instance limiter | I3-PREP lens (second lens's "I3R-5"): `limiter(60, 60_000)` in `app/api/client-errors/route.ts:11,18` is one counter per server instance, checked before the body is parsed, so 60 junk POSTs a minute make every real report that minute a 429; not listed as a risk | App code (`lib/deploy/report.ts:120-131`, route) but the finding is a documentation gap | **No code change** (the lens: "No code change needed for PREP"; counting only parsed reports would not help — a forged valid body is as cheap as junk; per-client limiting is the Vercel Firewall rule [OP]). The risk line → **WR-AM1-2** |

### R-1 — P08 walks until the cursor is null

- Test: each capped read (ledger, jobs of the capped CONSUMER_2) is walked at limit 100 until
  `next_cursor` is null (bounded at ceil(n/100)+1 pages); for every page, `items.length === 100`
  iff it carries a cursor; the union of pages equals the durable ids. A diagnostic prints rows/pages.
- `credit_world.py`: `U1R_CAPPED_JOBS` / `U1R_CAPPED_LEDGER` (default 120 each = the current world).
- **Fails-before** (old P08, correct adapter, heavy world 155/2000): P08 `not ok` —
  `'2026-09-20 12:00:00+00|e65a…' !== null` at credit-pg.test.ts:395; 7 pass / 1 fail.
- **Passes-after**: default world 8/8 (ledger 121 rows in 2 pages, jobs 121 in 2); heavy world
  8/8 (ledger **2001 rows in 21 pages**, jobs **156 in 2**).
- **Mutant** (hand, on `credit-reads.ts` rpcPage, restored after): "a walk that stops after the first
  follow-up page" = `next_cursor … && page.cursor === null ? …` (only the first page carries a
  cursor). Default world: P08 survives (the second page is the last anyway; P02/P03/P07 fail at
  their small limits); heavy world: **P08 killed** — `ledger: page 2 has 100 rows and cursor null`
  (4 fail: P02, P03, P07, P08). Not added to the U runner: P08 needs the database, which that runner
  skips (credit-pg is outside its SUITE, as for U1R-M41..M43).

### I3R-6 — every error boundary reports through I3's client

- `lib/deploy/error-view.ts`: the report effect is extracted as `useErrorReport(error)` (unchanged
  body: one keepalive POST of `browserReport(error, pathname)` = `{digest, route, name}`, errors
  swallowed); `ErrorView` calls it; the three segment boundaries destructure `error` and call it.
  They still render only their own copy (no message, no digest).
- New `tests/i3/boundaries.test.ts` **I3-BOUND-01**: discovers every `error.tsx`/`global-error.tsx`
  under `app/` (5 today, so a future boundary is held to the rule), renders each for real
  (TypeScript transpile + React server renderer; for `lib/deploy/` only, `react`'s `useEffect`
  runs at once so the report the browser sends after mount is observable), with a stub `fetch` and
  a path `/usage/someone.person%40example.com`: exactly one POST to `/api/client-errors`,
  `keepalive: true`, JSON content type, body exactly `{digest:"4242@E1", route:"/usage/[id]",
  name:"TypeError"}`, and no message/email/key in the markup.
- **Fails-before** (base boundaries): `app/(console)/billing/error.tsx sent 0 reports` (0 !== 1).
- **Mutants** (U runner, `tests/i3/boundaries.test.ts` added to its SUITE): I3-M01 usage / M02
  billing / M03 traces boundary never reports, M04 root error page never reports, M05 the report
  carries the message, M06 no keepalive — **6/6 killed** by I3-BOUND-01.
- Foreign tests adapted (the boundaries' signature is now `{ error, retry }`): U1-T19
  (`usage-view-model.test.ts`) accepts `{ error, retry }` and strips exactly the hook import and
  `useErrorReport(error);` before its "error never referenced" check; V1-V11
  (`trace-view-model.test.ts`) accepts `({ error, retry }`. Re-anchored: U1-M48 and V BOUNDARY-01
  (both still killed).

## Commands (at `4479539d` unless noted)

| Command | Exit | Counts |
|---|---|---|
| `make api-env`; `cd apps/app && pnpm install --frozen-lockfile` | 0 | — |
| `make console-test` | 0 | 664 tests: 610 pass, 0 fail, 54 skip (task-local stack/DSN skips) |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings |
| `make console-typecheck` | 0 | typegen + tsc clean |
| `make console-built` (at `9f842af5`; the later commit only touches mutant lists) | 0 | build ok; i2a 22/22 |
| `make console-mutants` | 0 | contracts 212/212; V 40/40; U 218/218 (+6 I3-M01..M06; 2 self-checks); C 185/185; A 46/46; A catalog 46/46 |
| `make console-c0-real` (app-c0) | 0 | 15/15 |
| `INFRX_D_TASK=app-u1r … credit_world.py` (default 120/120) | 0 | 8/8; ledger 121 / 2 pages, jobs 121 / 2 pages |
| `INFRX_D_TASK=app-u1r U1R_CAPPED_JOBS=155 U1R_CAPPED_LEDGER=2000 … credit_world.py` | 0 | 8/8; ledger 2001 / 21 pages, jobs 156 / 2 pages |
| same heavy world, old P08 (at base test, fails-before) | 1 | 7 pass / 1 fail (P08) |
| same heavy world, R-1 hand mutant | 1 | 4 fail incl. P08 (killed) |
| WR-AM1-1 in a `--shared` clone at `9f842af5`: `pytest tests/integration/ops/test_i3_rollback.py` with the test only | 1 | 19 passed / 2 failed (rb09 v2.1-vs-v3.0, v2.9-vs-v3.1) |
| same, with the rollback.py hunk: `pytest tests/integration/ops tests/integration/backend/recovery/test_runbooks.py` | 0 | 44 passed |
| WR-AM1-2 applied in the clone: same command | 0 | 38 passed (doc only) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | 4 PASS |

`infra/alerts` untouched, so `tests/i/test_observe.py` was not needed.
`git diff --stat 9b21339a..4479539d`: 11 files, all under `apps/app/` (the three boundaries,
`lib/deploy/error-view.ts`, `tests/i3/boundaries.test.ts`, `tests/u/credit-pg.test.ts`,
`tests/u/credit_world.py`, `tests/u/run-mutants.mjs`, `tests/u/usage-view-model.test.ts`,
`tests/v/trace-view-model.test.ts`, `tests/v/mutants.json`).

## Wiring requests

**WR-AM1-1** (I3R-5; owner of `infra/app/` + `tests/integration/ops/`, coordinator at merge) —
the MAJOR must match. Mutant: the base tuple compare = rb09's two red cases; dropping the MAJOR
check (`app[1] <= backend[1]`) fails the (3,0)-vs-(2,9) case.
```diff
--- a/infra/app/rollback.py
+++ b/infra/app/rollback.py
@@ -22,7 +22,7 @@ Checks, each printed with its evidence:
   contract    the tree's pinned contract surface (SURFACE_VERSION, contracts-vMAJOR.MINOR in
-              apps/app/lib/contracts/v2/money-units.ts) <= the gateway release's
+              apps/app/lib/contracts/v2/money-units.ts): same MAJOR, MINOR <= the gateway release's
               (apps/infrx-api/infrx/contracts/v2/__init__.py)
@@ -99,7 +99,8 @@ def judge(ref: str, applied: str, gateway_ref: str, repo: Path = REPO,
     fmt = lambda v: f"contracts-v{v[0]}.{v[1]}" if v else "none"   # noqa: E731
-    check("contract", app is not None and backend is not None and app <= backend,
+    # Same MAJOR (a MAJOR change is not compatible either way), MINOR <= the gateway's (I3R-5).
+    check("contract", app is not None and backend is not None and app[0] == backend[0] and app[1] <= backend[1],
           f"App pins {fmt(app)}; gateway {gateway[:12]} serves {fmt(backend)}")
--- a/tests/integration/ops/test_i3_rollback.py
+++ b/tests/integration/ops/test_i3_rollback.py
@@ -155,6 +155,24 @@ (after test_i3_rb06_the_comparison_mutants_are_caught)
+@pytest.mark.parametrize("app, gateway, ok", [
+    ((2, 1), (2, 1), True), ((2, 0), (2, 1), True), ((2, 1), (2, 0), False),
+    ((2, 1), (3, 0), False),   # review I3R-5: (2, 1) <= (3, 0) as a tuple passed
+    ((3, 0), (2, 9), False), ((2, 9), (3, 1), False),
+])
+def test_i3_rb09_the_contract_needs_the_same_major(tmp_path, app, gateway, ok):
+    """Catches (I3R-5): a (MAJOR, MINOR) tuple compare, which passes an App pinned to v2.1 against
+    a gateway serving v3.0; the MAJOR must match and the App's MINOR be <= the gateway's."""
+    repo = tmp_path
+    sh(repo, "init", "-q")
+    target = commit(repo, {**migrations("0001"), **IDENTITY,
+                           APP_CONTRACT: f'export const SURFACE_VERSION = "contracts-v{app[0]}.{app[1]}";\n'}, "App")
+    serving = commit(repo, {GATEWAY_CONTRACT: f'SURFACE_VERSION = "contracts-v{gateway[0]}.{gateway[1]}"\n'}, "gateway")
+    code, result, _ = run(repo, target, "--applied", "0001", "--gateway", serving)
+    assert (code, result["verdict"]) == ((0, "COMPATIBLE") if ok else (1, "REFUSED")), result
+    assert failed(result) == (set() if ok else {"contract"}), result
+
+
```

**WR-AM1-2** (I3R-8 + limiter + I3R-6 doc; owner of `infra/app/operations.md`) — three hunks
(ops01/ops06 and test_runbooks stay green, 38 passed):
```diff
@@ -118,7 +118,10 @@ (Browser errors)
-  · release <commit 7>`, a Try again button — and POST once to `/api/client-errors`.
+  · release <commit 7>`, a Try again button — and POST once to `/api/client-errors`. The
+  nearer segment boundaries (`app/(console)/usage`, `billing`, `traces` `error.tsx`) catch
+  first; they keep their own copy and POST the same report (`useErrorReport`, test
+  `I3-BOUND-01`).
@@ -151,13 +154,15 @@ (Limits)
-  VERIFIED: available on the project's plan).
+  VERIFIED: available on the project's plan). The per-instance ceiling is shared by every
+  client, so 60 junk posts a minute mask real reports on that instance for the rest of the
+  minute; those 429s show in the Vercel request log, and a 429 spike there is itself a signal.
@@ (Where it lands)
-  the Vercel plan's ⚠️ TO BE VERIFIED [OP]; a log drain is optional and not configured [OP].
+  the Vercel plan's ⚠️ TO BE VERIFIED [OP]; a log drain is optional; none is configured by this lane, hosted state ⚠️ TO BE VERIFIED [OP].
```
(Also stale, not mine: operations.md:155 "until WR-I3-1 makes `/api/client-errors` public" — WR-I3-1
is applied, `middleware.ts:5` lists it.)

## Deviations

Two foreign test files adapted (U1-T19 in `tests/u/usage-view-model.test.ts`, V1-V11 in
`tests/v/trace-view-model.test.ts`) plus their mutant anchors (`tests/u/run-mutants.mjs` U1-M48,
`tests/v/mutants.json` BOUNDARY-01): their source regexes pinned the old `{ retry }` signature; the
I3 mutants live in the U runner (no I3 runner exists).

## Remaining effort

Optimistic 0.25 h / likely 0.5 h / pessimistic 1 h, confidence high — merge plus WR-AM1-1/2 by
their owners; nothing left in the App.
