# App operations — combined checks, cutover, error monitoring, alerts, rollback (I3)

**Operator-run, preparation only.** The I3-PREP lane wrote this runbook, the offline tool
[`rollback.py`](rollback.py), the App's error reporting (`apps/app/lib/deploy/report.ts`,
`error-view.ts`, `release.ts`, `app/error.tsx`, `app/global-error.tsx`,
`app/api/client-errors/route.ts`, `instrumentation.ts` `onRequestError`), the App alert rule
[`infra/alerts/app.json`](../alerts/app.json) and their tests (`apps/app/tests/i3/`,
`tests/integration/ops/`). It changed nothing on Vercel, hosted Supabase, DNS, AWS or the
pilot box and read none of them. Every **[OP]** item is held by the operator; every hosted
fact is **[OP]** or ⚠️ TO BE VERIFIED; nothing here is a live-state claim.

It extends, never repeats, two runbooks: the App release runbook [README.md](README.md)
(I2A: environments, variables, P-05, release identity, deploy, smoke S1–S6) and the backend
runbooks [../runbooks/README.md](../runbooks/README.md) (I3B/I8: restart, restore, rollout,
rollback, observe, reconcile). Their rules hold here: log before you act, names never
values, never repair money by hand.

## Combined checks

Run after **every** App release and after **every** backend release, rollout or rollback (a
backend change alters what the App depends on). C2 runs offline; the rest are [OP].

| # | Check | Pass |
|---|---|---|
| C1 | App identity: `curl -sS https://app.callbill.ai/api/version` [OP] | `commit` = the recorded App release, `environment` = `production`, `apiOrigin` = the accepted edge origin |
| C2 | Backend compatibility (offline): `python3 infra/app/rollback.py <App release> --applied <hosted applied> --gateway <backend release>` with the two hosted facts from the session record [OP] | exit 0 `COMPATIBLE`; `expected_version_commit` = C1's `commit` |
| C3 | The backend release the edge runs is the accepted one (`infrx_build_info`, [../runbooks/rollout.md](../runbooks/rollout.md) W13 record) [OP] | same release as C2's `--gateway` |
| C4 | Smoke S1–S6 of [README.md](README.md#6-smoke-checks-after-deploy), unchanged: docs origin (S2), private caching (S3), signup callback (S4), preview safety (S5), static assets (S6) | every row passes |
| C5 | Maintenance rendering: while the edge serves maintenance (EdgeInMaintenance, [../runbooks/observe.md](../runbooks/observe.md#edge)) sign in and open `/models` and `/docs` [OP] | the catalog-unavailable notice, no model/limit/price shown; `/usage`, `/billing`, `/api-keys` still render (they read Supabase, not the gateway) |

What an App release depends on in the backend, per its tree: the contract surface
`SURFACE_VERSION` (`apps/app/lib/contracts/v2/money-units.ts`; `contracts-v2.1` at this
lane's base) served by the gateway (`apps/infrx-api/infrx/contracts/v2/__init__.py`); the
migrations through its newest (`0021` at this lane's base; the applied number on hosted is an
input, never read here); and the rulings it implements — R134 (deployment configuration),
R140 (key creation and the grant claim), R141 (request detail through `consumer_jobs` /
`consumer_job_result`), R142 (key plaintext, Settings) and R143 (operator writes through the
`operator_*` RPCs of migration 0025; until 0025 is applied the App answers "not confirmed")
([08 §10](../../research/plan/08-contracts-v1-encoding.md)). C2 checks the first two
mechanically; the rulings move only with a contract or migration change, which C2 then sees.

**Maintenance behaviour (what the App shows when the gateway drains or is rolled back).**
The App's only server-side call to the gateway is the published catalog, `GET /v1/models`,
on the Models and Docs pages (`app/(console)/models/catalog.ts` `loadCatalog`, 5 s timeout,
`cache: "no-store"`): any non-200 — the maintenance `503` included — or a timeout renders
`CATALOG_UNAVAILABLE` ("The model catalog is unavailable right now…",
`app/(console)/docs/content.ts`) and never a stale price (test `I3-COMPAT-01`). Every other
App page reads Supabase and is unaffected by the edge. With the admission flags off
([../runbooks/rollback.md](../runbooks/rollback.md#maintenance)) external API calls get
`503` with `Retry-After`; the Docs tell clients to retry after it **with the same
Idempotency-Key**, which answers with the original job rather than a second paid one, and no
App control submits inference (R141), so a maintenance window can not produce a
double spend through the App.

## Auth and credit cutover

The ordered operator sequence that opens consumer v1 on hosted. Each row is **[OP]**; its
abort column is where the operator stops and how that step is undone. A step runs only
after the previous step's check passed. Nothing here converts a balance, relabels USD or
moves a job between regimes ([transition.py](../../apps/infrx-api/infrx/operations/transition.py)).

| # | Step | Check after | Abort / rollback |
|---|---|---|---|
| X0 | [OP] Log purpose, the current known-good App deployment id, the backend release and hosted's applied migration in the session record; hold the deployment lock ([../README.md](../README.md)) | the entry exists | nothing changed: stop |
| X1 | Offline prerequisite: C2 with the App release to open and hosted's applied number | exit 0; if `migrations` fails, the missing migrations go through the backend window first ([../runbooks/rollout.md](../runbooks/rollout.md) W6–W7, fresh backup per [../runbooks/restore.md](../runbooks/restore.md)), never through the App | nothing changed: stop |
| X2 | [OP] BACKEND-READY accepted (E4C) and hosted migrations applied through the App release's newest by the backend window | `migrate.py plan` reports nothing pending; reconcile drift 0 ([../runbooks/reconcile.md](../runbooks/reconcile.md#drift)) | the backend's own rollback rows ([../runbooks/rollout.md](../runbooks/rollout.md)); the App is not deployed yet |
| X3 | [OP] App variables in Vercel Production ([README.md](README.md#7-operator-inputs) item 2), names checked, values never printed | every production-required name present | remove the new values; nothing is served from them yet |
| X4 | [OP] Deploy the App release (gate APP-MERGE, [README.md](README.md#5-release-identity-deploy-and-rollback-i8-discipline)) | C1–C5; record the known-good App release | [App rollback](#app-rollback) to the previous known-good deployment |
| X5 | [OP] P-05 auth settings on the production project with public signup still **off** ([README.md](README.md#4-hosted-auth-settings-p-05-op)); staging first | S4 passes on staging; the production dashboard shows each value (operator's read) | revert the changed setting |
| X6 | [OP] Regime: `python -m infrx.operations.cli credit-transition --dry-run --card <P-01 card> --input-rate <r> --output-rate <r>` (read-only report: flags, in-flight jobs, USD statements, drift), then the same without `--dry-run` plus `--drain-timeout-s 600 --idempotency-key <k> --reason "<why>"`; it enables `credit_admission` and `signup_grant` (audited once); the coordinator restarts gateway and worker with the `restart_with` it prints. If the backend window already moved to CREDIT (E4C), only the dry run's check applies | `blockers` empty, `applied` lists the flags; drift 0; the backend smoke (rollout.md W12) | `credit-transition --to legacy_usd …` (freezes CREDIT admission; accepted CREDIT jobs settle in CREDIT) or maintenance ([../runbooks/rollback.md](../runbooks/rollback.md#maintenance)) |
| X7 | [OP] Open public signup: "Allow new users to sign up" on the production project | X8 | turn it off: existing users keep signing in |
| X8 | [OP] First verified signup: a fresh operator-owned address → confirmation email → `/auth/callback` → `/welcome` shows 10,000 CREDIT; open the callback link again | `python -m infrx.operations.cli account --user <uuid>` shows the CREDIT wallet at exactly 10,000 available after the repeat (a second grant would read 20,000); drift 0 | [Cutover rollback](#cutover-rollback); the grant itself stays |
| X9 | [OP] Record the outcome, the release identities and the first user's id; release the lock | the record exists | nothing to undo: a record; the lock is released only after it |

⚠️ TO BE VERIFIED [OP]: the hosted flag state today. A 2026-09-24 handoff records
`signup_grant` set true for the legacy pilot's A1 grants ([rollout.md](../runbooks/rollout.md)
W7d); X6's dry run prints the current flags before anything is changed.

### Cutover rollback

In this order, each step logged, stopping as soon as the cause is contained:

1. **Close signup** [OP]: "Allow new users to sign up" off. New accounts stop; signed-up
   users keep signing in.
2. **Signup grant flag off** [OP]: no CLI verb turns `signup_grant` off on its own
   (`credit-transition` only enables it; GAP-I3-1 asks G8's owner for one). Until then it is
   the logged flag write of [rollback.md](../runbooks/rollback.md#maintenance) with
   `where name = 'signup_grant'`, your name and reason — a flag, never money. A claim then
   answers `unavailable` and `/welcome` offers a retry (`app/(auth)/flow.ts` `claimOutcome`).
3. **App release at fault**: [App rollback](#app-rollback).
4. **CREDIT admission must stop**: maintenance or `credit-transition --to legacy_usd`
   (X6's abort column).

**Not rolled back**: money — ledger entries, signup grants (never reversed; P-05 decision)
and settled jobs stay; wallets move only through the ledger trigger
([../runbooks/README.md](../runbooks/README.md) rule 5) and a correction is an audited
`adjust` with a reason; users — accounts stay (deletion is R85's retire action, not a
rollback); keys — revoke one with `revoke-key` if needed; migrations — never rolled back to
roll back code ([rollback.md](../runbooks/rollback.md#code-only-rollback-of-a-migration)).

**Legacy USD pilot owners.** Their USD history stays readable in USD on Billing
(`console_legacy_usd_statement`, C0) and is never converted or relabelled (R72,
[02-credits](../../research/platforms/02-credits.md#existing-usd-records-do-not-relabel-them)).
After X6 the legacy regime admits nothing new; accepted USD jobs settle in USD. An owner
whose organizations still hold a nonzero USD balance gets `rollout_hold` from the grant
claim (0015 `individual_usd_hold`), so no 10,000 CREDIT until that balance is resolved; the
App does not show the reason (R140). Who that is: X6's dry run lists `usd_statements`.
Resolving a balance is a separate operator decision [OP], never part of the cutover.

## Browser error monitoring

The App reports to itself; Vercel's function logs are the sink. No SDK, no vendor, no new
dependency.

- **Server errors**: `instrumentation.ts` `onRequestError` logs one line for every error
  Next captures (render, route handler, server action, middleware). Next still prints its
  own error log line; this line is the greppable index.
- **Browser errors**: `app/error.tsx` (a segment failed) and `app/global-error.tsx` (the
  root layout failed) render one safe page — "Something went wrong", `Reference <digest>
  · release <commit 7>`, a Try again button — and POST once to `/api/client-errors`.
- **The line**, one JSON object per error (`lib/deploy/report.ts` `ErrorLine`; test
  `I3-SHAPE-01` pins this example's keys to the code):

```json
{"event":"app_error","source":"browser","commit":"<40-hex or unknown>","deployment":"<dpl_... or unknown>","environment":"production","route":"/usage/[token]","digest":"1234567890","message":"<redacted, at most 300 characters; null on server lines>"}
```

- **Never in a report**: API keys (`sk-…`), JWTs and bearer tokens, `key=value` secrets,
  emails, URLs (credentialed or not), UUIDs and other long opaque runs (user, request and
  token ids), the query string and fragment (a callback's `token_hash`), request headers and
  cookies, the stack, the prompt, a request or result body. Server lines carry the route
  **pattern** (`/usage/[requestId]`) and no message at all. The digest is kept only in Next's
  shape (`<hash>` or `<hash>@E<code>`); anything else is dropped (tests `I3-SAN-01/02`).
- **Limits**: a body over 2 KiB is refused unread (413); not `application/json` 415; not a
  JSON object 400; an `Origin` other than the App's own 403; 429 past 60 reports a minute
  per server instance. Accepted is 204. No answer carries a body, so nothing is echoed
  (test `I3-ROUTE-01`). Per-client limiting is a Vercel Firewall rate-limit rule on
  `POST /api/client-errors` [OP] (⚠️ TO BE VERIFIED: available on the project's plan).
- **Anonymous pages**: until WR-I3-1 makes `/api/client-errors` public in the middleware, a
  report from a signed-out page (login, signup) is redirected to `/login` and lost; signed-in
  pages report.
- **Where it lands and how to read it**: the deployment's Runtime Logs in the Vercel
  dashboard (filter `app_error`), or `vercel logs <deployment url>` [OP]. Log retention is
  the Vercel plan's ⚠️ TO BE VERIFIED [OP]; a log drain is optional and not configured [OP].
  A user who quotes a page reference gives the digest: search for it; for a server-rendered
  failure the browser line and the server line carry the same digest.
- **Startup refusal** is not an `app_error` line: an incomplete environment logs
  `App environment refused (<env>): <variable names>` and every request answers 500
  ([README.md](README.md#1-order-relative-to-the-backend-window)); [App down](#app-down)
  catches it from outside.
- **Thresholds** — no App error metric reaches the box, so these are Vercel-side [OP]
  (a log alert or a drain into the P-25 destination), ⚠️ TO BE VERIFIED (P-25): engineering
  guesses until pilot traffic exists. **Page**: 10 or more `"event":"app_error"` lines with
  `"environment":"production"` in 10 minutes. **Ticket**: any `"source":"server"` line within
  an hour after a release (compare `commit` with C1).

## Alert delivery

What exists (I8): `infra/observe/observe.sh` on the box evaluates `alerts.json` +
`operations.json` over `host.prom`, `durable.prom` and `canary.prom`, and `deliver.py` sends
firing rules to the P-25 destination (BLOCKED until the destination exists,
[../runbooks/observe.md](../runbooks/observe.md#delivery-test)). `canary.sh` requests only the
public backend (`BASE=https://marlin2b.callbill.ai`, `/v1/chat/completions`). **Nothing
probes the App today, so no App condition is delivered**; a backend outage (CanaryFailed,
PublicEdgeDown) is the only App-visible effect covered, as the catalog-unavailable notice.

The smallest addition, both wiring requests to I8's owner (no edit to `infra/observe/` here):

- **WR-I3-2** `canary.sh`: one anonymous `GET <App origin>/api/version` per canary run,
  written as `infrx_app_up` (1 only for a 200 naming `production` and a full commit). No key,
  no body; before the canary-key check, so it runs even when the canary is not configured.
- **WR-I3-3** `rules.py`: merge [`infra/alerts/app.json`](../alerts/app.json) when present
  (version `a<alerts>+o<ops>+p<app>`); its one rule, `AppDown`, pages through the same
  delivery. The delivery test stays `74-alert-test.sh` (observe.md). I8's own tests follow:
  the merged version pin (`a1+o2+p1`) and `infra/app` in its mutant runner's tree copy.

The probe runs with the canary timer, which `72-observe-install.sh` enables only with
`P24_APPROVED` ([../runbooks/observe.md](../runbooks/observe.md#canary)); without it
`AppDown` cannot fire.

Browser and server error rates stay Vercel-side ([Browser error monitoring](#browser-error-monitoring)).

### App down

`AppDown`: the box could not read a production release identity from the App.

1. `curl -sS -D - https://app.callbill.ai/api/version` from anywhere [OP].
2. **500 on every path**, Runtime Logs show `App environment refused (production): …`: a
   variable is missing or wrong ([README.md](README.md#3-hosting-settings-names-only-values-live-in-vercel-never-in-the-repo)).
   Fix it in Vercel [OP] and redeploy the same commit, or [roll back](#app-rollback).
3. **200 but `commit` is `unknown` or `environment` is not `production`**: an unidentified or
   mis-scoped build is serving production: [roll back](#app-rollback) to the known-good.
4. **Timeout, DNS or TLS failure**: Vercel's status and the domain's DNS record
   (Route 53 CNAME, README §2) [OP].
5. After recovery: the [combined checks](#combined-checks).

## App rollback

**Known-good App release** = the record of [README.md](README.md#5-release-identity-deploy-and-rollback-i8-discipline):
`{commit, deployment, UTC, smoke output}` in the session record, written after a passing
S1–S6. None exists yet [OP]. A target must also satisfy the compatibility rule, which
`rollback.py` executes on the target's own tree:

> the target tree's newest migration ≤ hosted's applied migration **and** the target's pinned
> backend contract (`SURFACE_VERSION`) ≤ the running gateway's **and** the target has I2A's
> release identity.

```bash
# coordinator host, repository root, read-only: the two hosted facts come from the record
python3 infra/app/rollback.py "$APP_KNOWN_GOOD_COMMIT" --applied "$HOSTED_APPLIED" \
    --gateway "$BACKEND_RELEASE"      # exit 0 COMPATIBLE, 1 REFUSED (reason per check), 2 usage
```

1. **Log** the trigger, the serving and target deployment ids and C2's output [OP].
2. **Judge** the target with the command above. `REFUSED` on `contract` means the backend
   was rolled back below the target's contract: choose an older known-good whose contract
   the gateway serves, or keep the backend in maintenance. Never fix forward on production
   without a new release identity.
3. **[OP] Vercel Instant Rollback** (promote the recorded deployment id; no rebuild).
   ⚠️ TO BE VERIFIED [OP]: after an instant rollback Vercel stops assigning the production
   domain to new deployments until the rollback is undone, and the promoted deployment keeps
   the environment values it was built with — a variable changed or a key rotated since
   then is not in it.
4. **Smoke**: C1 must show `commit` = `expected_version_commit` from step 2; then S1–S6.
5. **Record** the rollback as the current release; the previous one is no longer known-good
   until re-proven.

### Backend rollback and the App

A backend rollback ([../runbooks/rollback.md](../runbooks/rollback.md)) runs behind the
maintenance edge: the App shows the catalog-unavailable notice on Models and Docs (C5) and
every Supabase-backed page keeps working; external clients get `503` + `Retry-After` and
retry with the same Idempotency-Key, so a replay never becomes a second charge. Accepted
jobs settle in their own regime; the App's usage and request detail show them once settled
(an unknown-usage result is R141's known gap). Once CREDIT admission is on, only a metering
runtime is a backend target (rollback.md) — this changes nothing in the App. After the
backend rollback, rerun C2 with the new `--gateway`; a contract refusal is step 2 above.

## Retention and resource budgets

- **Private caching**: every App response except Next's static assets is `private,
  no-store`, and no private page is prerendered (R134, `lib/deploy/cache.ts`). Result content
  reaches the browser only from `GET /usage/<id>/result`, is kept in memory only and dropped
  at the persisted `result_expires_at`; expiry is the database's, never a clock in the App
  (R141). Key plaintext and results are never in browser storage, a URL, a log or an error
  report (R142; this runbook's report never carries a body).
- **Content TTLs and caches**: the approved values — result, stream journal, cache and
  source retention, idempotency, the retention grace, the processing-cache high/low water
  (50 GiB) and the sweep intervals — are the P-25 decision
  ([15-pending-inputs.md](../../research/plan/15-pending-inputs.md), P-25 row), enacted by
  P25-ENACT (`1c62eef8`, merging with the backend union round 3; not in this lane's base).
  The App sets none of them, never extends retention and states limited-period storage on
  Settings (R142); the retention alerts are the backend's
  ([../runbooks/observe.md](../runbooks/observe.md#retention-stalled)).
- **Resources**: the App has no footprint on the box. Its database traffic goes through
  Supabase's API with the user's session or the service-role key, not the runtime pooler that
  `pool_budget.py` sizes ⚠️ TO BE VERIFIED [OP] against the project's connection limits.
  Vercel function limits are the plan's [OP].
- **No payment stack**: no checkout, card or billing provider is added. CREDIT enters only by
  the one-time 10,000 CREDIT signup grant per verified individual and audited operator
  adjustments (R143: grant and rate publication stay CLI-only); USD history is read-only.

## Scenario register

OPS-RECOVER's App half. `offline` rows run in this repository (their test ids exist,
`test_i3_ops05`); `NOT RUN` rows need hosted, Vercel or the box and say why. DUR-OUTBOX is
the backend's (I3B): lost Valkey data/acks, replayed delivery and a restarted dispatcher are
drilled there (`research/plan/evidence/i/I3B-32f94b3.md`); the App has no outbox and is not
re-drilled.

| ID | Scenario | Status |
|---|---|---|
| OPS-APP-01 | A rollback target is judged by its own tree: migrations, contract, identity | offline: test_i3_rb01, test_i3_rb02, test_i3_rb03, test_i3_rb04, test_i3_rb05, test_i3_rb06, test_i3_rb07 |
| OPS-APP-02 | Cutover prerequisite: the migration numbering is contiguous and C2 accepts the tree at its own newest | offline: test_i3_ops02 |
| OPS-APP-03 | Every cutover step names its check and its abort | offline: test_i3_ops03 |
| OPS-APP-04 | The App alert rule names a section here, merges without a clash and fires on its metric | offline: test_i3_ops04 |
| OPS-APP-05 | The canary's App probe (WR-I3-2 composed) writes 1 only for a production identity | offline: test_i3_ops05 |
| OPS-APP-06 | Error reports carry no secret; the report route refuses without echo; the error page shows no message or stack | offline: I3-SAN-01, I3-SAN-02, I3-SHAPE-01, I3-ROUTE-01, I3-ROUTE-02, I3-PAGE-01, I3-REL-01, I3-INST-01 |
| OPS-APP-07 | A draining gateway's 503 renders the catalog-unavailable state | offline: I3-COMPAT-01 |
| OPS-APP-08 | The hosted cutover X0–X9 | NOT RUN: hosted Supabase, Vercel and the box; needs BACKEND-READY, P-01 card, P-05 settings, the I2A live half |
| OPS-APP-09 | Vercel Instant Rollback to a known-good App release, then C1 and S1–S6 | NOT RUN: no deployed known-good App release exists yet (I2A live half) |
| OPS-APP-10 | AppDown reaches the P-25 destination | NOT RUN: needs WR-I3-2/3 on the box, the P-25 destination and `74-alert-test.sh` |
| OPS-APP-11 | A browser and a server `app_error` line appear in Vercel's Runtime Logs | NOT RUN: needs a deployment [OP] |
| OPS-APP-12 | A backend rollback with the App open (maintenance rendering, C5 live) | NOT RUN: a box window, coordinator ([../runbooks/rollback.md](../runbooks/rollback.md)) |

## Verification log

- 2026-09-26: Written by I3-PREP with the code, tool, rule and tests it cites; local checks
  only (console tests, lint, typecheck, `next build`, `tests/integration/ops`). No hosted,
  Vercel, DNS, AWS or box state was read or changed.
