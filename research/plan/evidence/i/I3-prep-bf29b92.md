# I3-PREP — App recovery, alarms and rollback (preparation half) — evidence

- Task: I3 "Recovery, alarms and rollback runbooks" (product shared/app). Lane I3-PREP, branch
  `codex/i3-prep`, worktree `.claude/worktrees/codex-i3-prep`.
- Base `6badd4e1` (claude/consumer-v1 tip). Code head `bf29b92b` (commits `ecd81833`,
  `bf29b92b`); this file and the update JSON are committed on top.
- **No gate is claimed; the manifest status stays `planned`.** Nothing hosted, Vercel, DNS, AWS
  or box was read or changed. No task-local database was needed, so `tasklocal.py` is
  unchanged; the one local `next start` probe used port 55461 (this lane's number), stopped after.

## Deliverables

| Path | What |
|---|---|
| `infra/app/operations.md` (new) | Combined checks C1–C5 (identity vs accepted backend, C2 offline compatibility, docs origin / caching / S1–S6 by reference, maintenance rendering), what an App release depends on (SURFACE_VERSION, migrations, R134/R140–R143), auth/credit cutover X0–X9 with check + abort per step, cutover rollback (what is not rolled back; legacy USD owners' continuity), browser error monitoring (line shape, never-contains, limits, sink, reading, Vercel-side thresholds), alert delivery (what I8 covers; WR-I3-2/3), `#app-down`, App rollback (known-good record, compatibility rule, Vercel instant rollback [OP]), backend rollback and the App, retention/resource budgets (P-25 by reference, no payment stack), OPS-RECOVER App-half scenario register, verification log |
| `infra/app/rollback.py` (new) | Offline judge of an App rollback/deploy target: commit resolves (prints the expected `/api/version` commit), I2A identity present, newest migration ≤ `--applied`, pinned `SURFACE_VERSION` ≤ the `--gateway` release's. Exit 0/1/2 |
| `apps/app/lib/deploy/report.ts` (new) | Client-safe `ErrorLine` shape, redaction (URLs, emails, `sk-` keys, JWT/bearer, key=value secrets, ≥32-char opaque runs incl. UUIDs), Next digest shape only, route without query/fragment (per segment), `parseClientReport` (415/413/400/204), `serverErrorLine`, `browserReport`, `shortCommit`, per-instance `limiter` |
| `apps/app/lib/deploy/release.ts` (new) | `runningRelease()` from I2A's `releaseIdentity` with the textual build-time reads |
| `apps/app/lib/deploy/error-view.ts` (new) | Safe error page body (createElement, renderable by `node --test`): digest reference + release short commit + Try again (`retry`); one keepalive POST to the report route |
| `apps/app/app/error.tsx`, `global-error.tsx` (new) | Mount `ErrorView`; global-error brings html/body/styles |
| `apps/app/app/api/client-errors/route.ts` (new) | POST only; Origin must be the App's (403); Content-Length > 2 KiB 413; 60/min per instance 429; parse → one `console.error` JSON line; bare-status answers with `private, no-store` |
| `apps/app/instrumentation.ts` (extended) | `onRequestError` logs `serverErrorLine(error, context.routePath, runningRelease())`; `register`/`assertDeployEnv` unchanged |
| `infra/alerts/app.json` (new) | `AppDown` (page, `infrx_app_up < 1`, runbook `infra/app/operations.md#app-down`), `pending_producers` naming WR-I3-2 |
| `infra/app/README.md`, `infra/runbooks/README.md` | one additive link line / one index row |
| `apps/app/tests/i3/report.test.ts` (new) | 9 cases: I3-SAN-01/02, I3-SHAPE-01, I3-ROUTE-01/02, I3-PAGE-01, I3-REL-01, I3-INST-01, I3-COMPAT-01 |
| `tests/integration/ops/` (new) | `test_i3_rollback.py` (rb01–rb07, 12 cases incl. the ≤→< mutant), `test_i3_operations.py` (ops01–ops06, 9 cases incl. WR-I3-2/3 composed) |

## Checks

| Check | Result |
|---|---|
| OPS-APP-01 rollback target judged by its own tree (rb01–rb07) | PASS (12/12) |
| ≤ → < mutant on the migration comparison (rb06, automated) | PASS: killed (the equal case refuses under the mutant) |
| OPS-APP-02 migration numbering contiguous 0001–0021, tool accepts HEAD at its newest (ops02) | PASS |
| OPS-APP-03 every cutover step X0–X9 has check + abort; C1–C5 have pass conditions (ops03) | PASS |
| OPS-APP-04 AppDown names an existing section, merges without clash (WR-I3-3 in a temp copy), fires on 0 / silent on 1, duplicate refused (ops04) | PASS |
| OPS-APP-05 canary App probe (WR-I3-2 in a temp copy) writes 1 only for a 200 production identity with a full commit; 0 for unknown commit, preview, 500; runs with no canary key (ops05 ×4) | PASS |
| OPS-APP-06 report redaction, route refusals without echo, safe page, documented shape, commit rule parity, instrumentation (I3-SAN/SHAPE/ROUTE/PAGE/REL/INST) | PASS (8/8) |
| OPS-APP-07 maintenance 503 / hung gateway → catalog unavailable, `no-store` (I3-COMPAT-01) | PASS |
| Runbook links/anchors/bash blocks/log; register rows name existing tests or a reason (ops01, ops06) | PASS |
| Bundle: no server-only name in `.next/static` (I2A-BUILT-01, after `pnpm build`), no private page prerendered (I2A-BUILT-02) | PASS |
| Local `next start` probe (WR-I3-1 temporarily applied, rebuilt, then restored) | PASS: valid POST 204, GET 405, text/plain 415, 3 KB 413, foreign Origin 403, `Cache-Control: private, no-store, max-age=0`; logged `{"event":"app_error","source":"browser",…,"route":"/usage/[token]","digest":"123","message":"x [email] [key]"}`; 0 occurrences of the email or key in the server log |
| OPS-APP-08 hosted cutover X0–X9 | NOT RUN: hosted Supabase, Vercel, box; needs BACKEND-READY, P-01, P-05, I2A live half |
| OPS-APP-09 Vercel Instant Rollback + C1/S1–S6 | NOT RUN: no deployed known-good App release yet |
| OPS-APP-10 AppDown delivered to the P-25 destination | NOT RUN: WR-I3-2/3 on the box, P-25 destination, `74-alert-test.sh` |
| OPS-APP-11 `app_error` lines in Vercel Runtime Logs (browser + server) | NOT RUN: needs a deployment [OP]. The server line is proven by unit test only (no locally reproducible server render error was driven) |
| OPS-APP-12 backend rollback with the App open | NOT RUN: box window, coordinator |
| DUR-OUTBOX | backend (I3B), not re-drilled: `research/plan/evidence/i/I3B-32f94b3.md` |

Fails-before (base `6badd4e1` tree via `git archive`, new tests copied in): `node --test
tests/i3/*.test.ts` → exit 1, `ERR_MODULE_NOT_FOUND lib/deploy/report.ts`; `pytest
tests/integration/ops` → collection error (`infra/app/operations.md` absent). Manual mutants on
the console side (each applied, run, restored): no email redaction → SAN-01, ROUTE-01 fail; any
digest accepted → SAN-02, PAGE-01; query kept → SAN-02, ROUTE-01; size limit off → ROUTE-01;
origin check off → ROUTE-01; page prints `error.message` → PAGE-01; limiter never resets →
ROUTE-02; commit rule drift → REL-01; unknown field logged → SHAPE-01, ROUTE-01. 9/9 killed.

## Commands (at `bf29b92b` unless noted)

| Command | Exit | Counts |
|---|---|---|
| `make api-env` | 0 | pinned env |
| `cd apps/app && pnpm install --frozen-lockfile` | 0 | — |
| `make console-test` (at `ecd81833` + test fix, identical code) | 0 | 646 tests: 595 pass, 0 fail, 51 skip (all pre-existing task-local-stack/DSN skips + 2 BUILT before the build) |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings |
| `make console-typecheck` | 0 | clean |
| `cd apps/app && pnpm build` (placeholder `NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:9`) | 0 | `/api/client-errors` ƒ dynamic |
| `node --test tests/i3/*.test.ts tests/i2a/*.test.ts` (after build) | 0 | 31/31 (I3 9, I2A 22 incl. both BUILT) |
| `node --test tests/i3 tests/i2a tests/a` | 0 | 109: 104 pass, 5 skip (A2 DSN, pre-existing) |
| `apps/infrx-api/.venv/bin/python -m pytest tests/integration/ops -q` | 0 | 21 passed |
| `pytest tests/integration/backend/recovery/test_runbooks.py` (index row added) | 0 | 8 passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | 4 PASS |
| Composed WR-I3-2/3 (temporarily applied, then `git checkout --` restored): `uv run pytest tests/i/test_observe.py` | 0 | 18 passed, 1 xfailed (pre-existing strict xfail) |
| same, `INFRX_MUTANTS=all pytest tests/i/test_mutants.py -k <the 14 mutants whose kill cases are in test_observe.py>` | 0 | 14 passed (without the `infra/app` copy line: 7 broken_runner — hence that hunk) |
| same, `pytest tests/integration/ops` | 0 | 21 passed (probe/merge taken as applied) |
| Composed WR-I3-1 (temporarily applied): `node --test tests/a/public-routes.test.ts` | 0 | 5/5 incl. I3-ROUTE-03 |

`git diff --stat 6badd4e1..bf29b92b`: 15 files, +1268, all owned paths (listed above).

## Wiring requests

**WR-I3-1** (App middleware; C0/A2 owner) — anonymous error reports. Until applied, a report
from a signed-out page (login, signup) is redirected to `/login` and lost.
```diff
--- a/apps/app/lib/supabase/middleware.ts
+++ b/apps/app/lib/supabase/middleware.ts
-const PUBLIC = ["/api/version", "/login", "/signup", "/verify-email", "/forgot-password", "/auth"];
+const PUBLIC = ["/api/version", "/api/client-errors", "/login", "/signup", "/verify-email", "/forgot-password", "/auth"];
--- a/apps/app/tests/a/public-routes.test.ts
+++ b/apps/app/tests/a/public-routes.test.ts
@@ -52,3 +52,9 @@ test("I2A-ROUTE-01 the release identity is public", () => {
+
+// I3 WR-I3-1: a signed-out page (login, signup) that fails must still reach the report route.
+test("I3-ROUTE-03 the browser error report route is public", () => {
+  assert.ok(isPublic("/api/client-errors"), "a signed-out page's error report would be redirected to /login");
+  assert.ok(!isPublic("/api/client-errorsx"), "only the exact report route is public");
+});
```
Proof: public-routes 5/5; rebuilt `next start` probe above (anonymous POST 204).

**WR-I3-2** (I8 owner, `infra/observe/canary.sh`) — the App probe; insert after the
`publish() { … }` line, and drop `pending_producers` from `infra/alerts/app.json` in the same
merge (ops04 enforces it):
```bash
# I3 (WR-I3-2): the App's public release identity, anonymous (WR-I2A-1): no key, no body.
# 1 only for a 200 naming production and a full commit. Before the key check: needs no key.
APP=${APP:-https://app.callbill.ai}
app_up=0
if curl -sf --max-time 15 -o "$work/app" "$APP/api/version" \
   && grep -q '"environment":"production"' "$work/app" \
   && grep -Eq '"commit":"[0-9a-f]{40}"' "$work/app"; then app_up=1; fi
m infrx_app_up "$app_up"
echo "canary app up=$app_up"
```
(Exact text = `CANARY_PROBE` in `tests/integration/ops/test_i3_operations.py`.)

**WR-I3-3** (I8 owner) — merge `app.json`:
```diff
--- a/infra/observe/rules.py
+++ b/infra/observe/rules.py
-    for rule in ops["rules"]:
+    # I3 (WR-I3-3): the App's rules, when present; same shape, same duplicate refusal.
+    app = json.loads((directory / "app.json").read_text()) if (directory / "app.json").is_file() else None
+    for rule in ops["rules"] + (app["rules"] if app else []):
         if rule["name"] in rules:
             raise SystemExit(f"rule defined twice: {rule['name']}")
         rules[rule["name"]] = rule
-    return {"version": f"a{alerts['version']}+o{ops['version']}", "rules": list(rules.values())}
+    version = f"a{alerts['version']}+o{ops['version']}" + (f"+p{app['version']}" if app else "")
+    return {"version": version, "rules": list(rules.values())}
--- a/apps/infrx-api/tests/i/test_observe.py
+++ b/apps/infrx-api/tests/i/test_observe.py
-    assert len(names) == len(set(names)) and RULES["version"] == "a1+o2"
+    assert len(names) == len(set(names)) and RULES["version"] == "a1+o2+p1"
--- a/apps/infrx-api/tests/i/mutants.py
+++ b/apps/infrx-api/tests/i/mutants.py
     for part in (("infra", "runbooks"), ("infra", "observe"), ("infra", "alerts"),
+                 ("infra", "app"),              # I3 (WR-I3-3): AppDown's runbook section
                  ("apps", "app", "supabase", "migrations")):
```
(Exact rules.py text = `RULES_OLD`/`RULES_NEW` in the ops test.) Proof: the composed rows above.

**WR-I3-4 (optional)** `Makefile`: add `tests/integration/ops` to a canonical target (it already
runs under `make integration`'s `pytest tests/integration`), e.g.
`ops-test: ; $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/ops`.

**SCOPE**: `apps/app/app/error.tsx`, `global-error.tsx` and `app/api/client-errors/route.ts` are
Next-fixed paths listed in this lane's brief; `lib/deploy/release.ts`, `report.ts`,
`error-view.ts` are new files under `lib/deploy/` (env.ts untouched).

## Open issues

- **GAP-I3-1**: no audited CLI verb turns `signup_grant` off alone (`credit-transition` only
  enables it); the cutover rollback uses the logged flag write of rollback.md#maintenance.
  Ask G8's owner for `flag --name signup_grant --off --idempotency-key --reason` (or accept).
- ⚠️ TO BE VERIFIED [OP]: hosted `signup_grant` state (a 2026-09-24 handoff set it true for the
  legacy pilot); Vercel instant-rollback semantics (domain auto-assignment, env values of the
  promoted build); Vercel log retention and Firewall rate-limit availability on the plan;
  Supabase API connection limits for the App.
- Error-rate thresholds (10 lines / 10 min page; any server line in the first hour ticket) are
  engineering guesses ⚠️ TO BE VERIFIED (P-25); Vercel-side delivery [OP].
- The per-instance 60/min ceiling is a log-flood bound, not per-client limiting (ponytail comment).
- The brief's "applied 0001–0023" is not this base's tree: the tree carries 0001–0021 (0022/0023
  are on the backend union; 0024/0025 in D10-APP-SQL). The tool takes the number as an input.
- P25-ENACT (`1c62eef8`) is not in this base; operations.md cites P-25 by reference only.

## What I3 proper still needs (operator run with evidence, not a build)

BACKEND-READY (E4C, P-17/P-18); P-01 approved card for X6; P-05 settings on both projects
(SMTP, captcha, rate limits, redirect allowlist) and a staging project (I2A §7); the I2A live
half (Vercel variables, APP-MERGE deploy, first known-good App record); P-24 canary approval (the
App probe rides the canary timer); P-25 alert destination + `74-alert-test.sh`; hosted applied
migration number and backend release for C2; WR-I3-1..3 merged. Then: X0–X9, C1–C5, one App
instant-rollback drill (OPS-APP-09), AppDown delivery (OPS-APP-10), `app_error` in Vercel logs
(OPS-APP-11), a backend rollback with the App open (OPS-APP-12).

## Remaining effort

Optimistic 3 h / likely 6 h / pessimistic 14 h, confidence low. Basis: the build is done; the
remainder is merge of WR-I3-1..3 (≈0.5 h), the operator cutover window and drills (≈3–5 h once
BACKEND-READY, P-01, P-05 and the I2A live half exist), fixes from the first hosted run. The
dates are gated on inputs outside this lane.
