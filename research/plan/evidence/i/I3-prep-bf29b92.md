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

## Fix round (review of handback `35647804`; code head `1bfce02a`)

Base `6badd4e1`; previous code head `bf29b92b`; fix commit `1bfce02a` (8 files, +337/−140, all
owned paths); this section and `updates/I3-20260926T0206Z.json` are committed on top. Tests were
written first and recorded failing against `bf29b92b` (below), then the code was changed. No
gate claimed; manifest stays `planned`. Nothing hosted, Vercel, DNS, AWS or box was read.

| Finding | Fix | Regression (fails-before at `bf29b92b`) |
|---|---|---|
| 0-I3R-1 (blocking), 1-I3R-3 | The browser report no longer sends or logs `message`. `ErrorLine.message` → `name`: the error's class name, kept only in the shape `^[A-Za-z]{0,40}(Error\|Exception)$`, else `null` (server lines carry it too). `route` is reduced segment by segment to an allowlist — the App's route words (lowercase letters, hyphens, optional leading `_`) and Next's patterns (`[requestId]`, `(console)`); every other segment (id, email, name, anything percent-encoded) reads `[id]`. `browserReport` applies the same reductions in the browser, so there is no client-side truncation before sanitising. `redact()` and its denylist are deleted. operations.md "What a report carries, and nothing else" states exactly this | I3-SAN-01 (the review's six leaks as cases: `%40` email, `{"password":"hunter2"}`, relative URL with `?email=`, V8 `JSON.parse` body excerpt, non-ASCII email, the 29-char straddled hex run; plus smuggled `name` values) and I3-SAN-02 (`/a/…%40…` → `/a/[id]`, `/a/Some%20Person/x` → `/a/[id]/x`) — both `not ok` before |
| 0-I3R-2 | Origin is judged by `Sec-Fetch-Site` (403 when present and not `same-origin`), never by `request.url`. A browser without the header cannot POST `application/json` cross-origin: the preflight gets Next's automatic `OPTIONS` (204, `allow: OPTIONS, POST`, no `Access-Control-*`, probed live). OPS-APP-11 now names the same-origin acceptance behind Vercel ⚠️ TO BE VERIFIED | I3-ROUTE-04: Origin `http://127.0.0.1:55461`, `https://app.callbill.ai`, `https://app.example.test` with `sec-fetch-site: same-origin` against a request.url of `https://app.example.test` → 204; before: 403 |
| 1-I3R-1 | The route reads `request.body` with a reader that stops once past 2 KiB and cancels the stream (413); a declared `Content-Length` over 2 KiB is still refused unread. The size check moved out of `parseClientReport` (the route guarantees it). operations.md states both paths | I3-ROUTE-05: a 64 MiB `ReadableStream` body with no Content-Length → 413 with ≤ 4 KiB pulled; before: `65537 KiB pulled before the refusal` |
| 1-I3R-2 | Cutover X4 = P-05 auth settings (signup still off, staging first), X5 = App deploy, matching README §1 item 2 (hosted inputs before the release) | ops03 asserts every step setting a hosted App input (`P-05`, `App variables`) precedes the deploy step; before: `X5 sets a hosted App input after the deploy (X4)` |
| 1-I3R-4 | `rollback.py`: `newest == applied` passes; `newest < applied` passes only with `--schema-proof NNNN` reaching `--applied` and `--evidence` files that exist inside this checkout (as `known-good.py`'s `schema_proof`); the refusal names the unproven range. operations.md App rollback states the rule and why (0021 revokes column grants, drops a policy). README §5 still says "additive": **WR-I3-5** below | rb08 (hosted ahead with no proof / proof short of applied / no evidence / missing evidence / evidence outside the checkout / evidence without a number → REFUSED; proof reaching applied → COMPATIBLE); rb01 reduced to the equal case; rb06 now kills two mutants (equality dropped; `newest <= applied` additivity assumed again). Before: rb08 and both rb06 cases failed |

Manual console mutants on the new code (each applied, run, restored): Sec-Fetch-Site check off →
ROUTE-01; `request.text()` back → ROUTE-01, ROUTE-05; old `request.url` origin rule back →
ROUTE-04; any `name` accepted → SAN-01; segments not reduced → SAN-02, ROUTE-01; browser sends
`message` → SAN-01, PAGE-01; server `name` dropped → SHAPE-01. 7/7 killed.

Live probe (`next start -H 127.0.0.1 -p 55461`, this lane's port, WR-I3-1 applied temporarily,
rebuilt, stopped, `middleware.ts` restored and rebuilt clean; placeholder public Supabase values,
`INFRX_APP_ENVIRONMENT=development`, local build so `commit` reads `unknown`): same-origin from
`http://127.0.0.1:55461` 204; `Host: app.callbill.ai` + Origin `https://app.callbill.ai` +
same-origin 204 (also with `x-forwarded-host/proto`); `sec-fetch-site: cross-site` 403,
`same-site` 403; `text/plain` 415; 3 KB declared 413; 64 MiB chunked with no length 413 (the
route stops reading; the Node server still drains the socket, so curl reports the whole upload —
the transport-level cap is Next's/Vercel's [OP]); GET 405; `Cache-Control: private, no-store,
max-age=0`; `OPTIONS` preflight from a foreign origin 204 with no `Access-Control-*`. A report
posted with the review's message (`user someone.person%40example.com / Some Person phone …`,
route `/a/someone.person%40example.com`) logged
`{"event":"app_error","source":"browser",…,"route":"/a/[id]","digest":"11","name":"TypeError"}`;
`name: "Error: hunter2"` logged `"name":null`; 0 occurrences of the email, name, phone or
`hunter2` in the server log. A non-browser client with a foreign Origin and no Sec-Fetch-Site
is accepted (it can forge any header; the per-instance ceiling and the Firewall rule [OP] bound it).

| Command (at `1bfce02a`) | Exit | Counts |
|---|---|---|
| `node --test tests/i3/report.test.ts` before the fix (stub `safeName` export so the module loads) | 1 | 11: 4 pass, 7 fail (SAN-01/02, SHAPE-01, ROUTE-01/04/05, PAGE-01) |
| `pytest tests/integration/ops` before the fix | 1 | 4 failed (ops03, rb06 ×2, rb08), 20 passed |
| `make console-test` | 0 | 648 tests: 599 pass, 0 fail, 49 skip (pre-existing task-local stack/DSN skips) |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings |
| `make console-typecheck` | 0 | clean |
| `cd apps/app && pnpm build` (placeholder public Supabase values) | 0 | `/api/client-errors` ƒ dynamic |
| `node --test tests/i3/*.test.ts tests/i2a/*.test.ts` (after build) | 0 | 33/33 (I3 11, I2A 22 incl. both BUILT) |
| `apps/infrx-api/.venv/bin/python -m pytest tests/integration/ops tests/integration/backend/recovery/test_runbooks.py` | 0 | 33 passed (ops 25: rb 15, ops 10; runbooks 8) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | 4 PASS |

**WR-I3-5** (I2A owner, `infra/app/README.md` §5) — state rollback.py's migration rule, so the
two App runbooks agree (exact text = `README_OLD`/`README_NEW` in
`tests/integration/ops/test_i3_operations.py`; ops07 composes it and checks the link resolves):
```diff
--- a/infra/app/README.md
+++ b/infra/app/README.md
@@ §5 Known-good App release
   Like `known-good.py` for the backend, a rollback target must be compatible with the applied
-  schema: its tree's newest migration ≤ the hosted applied migration (migrations are additive).
+  schema: its tree's newest migration = the hosted applied migration, or below it only with a
+  recorded schema proof (`infra/app/rollback.py --schema-proof`; not every migration is
+  additive, [operations.md](operations.md#app-rollback)).
```
WR-I3-1..4 are unchanged (WR-I3-1's `I3-ROUTE-03` id is still free: this round added ROUTE-04/05).

Open after this round: whether Vercel forwards `Sec-Fetch-Site` unchanged and accepts a
same-origin report on the custom domain (OPS-APP-11, ⚠️ TO BE VERIFIED [OP]); a schema proof
for any older known-good App target once hosted is ahead of it (none exists yet [OP]).

Remaining effort (unchanged basis): optimistic 3 h / likely 6 h / pessimistic 14 h, confidence
low — merge of WR-I3-1..3 and 5, then the operator cutover and drills once BACKEND-READY, P-01,
P-05, P-24, P-25 and the I2A live half exist.
