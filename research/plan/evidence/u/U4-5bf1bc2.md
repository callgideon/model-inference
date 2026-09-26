# U4: owned request detail and result lifecycle

## Task and status

- **Task:** U4 (track U, product app; oracles USER-RESULTS, RESULT-EXPIRY, CONSOLE-TENANT). Opus implementer, lane `app-u4`.
- **Status: implemented and handed back for review.** This lane was dispatched ahead of BACKEND-READY by the recorded user decision (session-03, 21:08Z). Implemented code does not accept APP-LOCAL or APP-PILOT.
- Base `46776646` (the claude/consumer-v1 tip). Predecessor U1R was merged first as `bd6ff5d2`: `git merge --no-ff codex/app-u1r` at `7482c21a`, with no conflicts. Implementation head is **`5bf1bc2a`**. This evidence file and the tracker update are committed on top of it; the handback gives the final head.
- Branch `codex/app-u4`, worktree `.claude/worktrees/codex-app-u4`. Nothing was pushed, rebased, amended or stashed.

Commits (oldest first, after the U1R merge):

```
da9d7265 U4: request detail/result lifecycle tests first (fail: modules absent)
cb177069 U4: owned request detail route — reads over consumer_jobs/consumer_job_result (content gated by the API's read classification), no-store result route, bounded poller, expiry-dropping result panel, retry guidance
6a47ee57 U4: a job read refused for a missing JWT subject is 'signed out' on the result route (test first)
5bf1bc2a U4: real-PostgreSQL oracle (request_world.py + request-pg.test.ts ...) and 25 U4 mutants in the U runner
```

## Changed paths (all inside the owned set)

| Path | What |
|---|---|
| `apps/app/app/(console)/usage/[requestId]/request-reads.ts` | `postgrestRequestReads(client, userId)` reads through the user's own Supabase session (anon key + JWT, RLS on). **job(id)** calls U1R's unchanged `postgrestCreditReads(...).jobs` with `p_request_id` injected, so it uses the same `consumer_jobs` RPC, the same fail-closed parser and the same error mapping. A malformed id is `null` with no query. A foreign or unknown id is `null`. A row for a different id is `null`. **result(id)** reads the job first. It calls `consumer_job_result` only when the contract's read classification is `available`. Otherwise it returns `pending` / `withheld` / `no_result` / `expired` / `unavailable`. It maps the RPC's typed refusals (`not_found:` / `result_pending:` / `result_expired:` P0001; 42501 / PGRST30x → `signed_out`). It never throws. **resultResponse(read)** returns JSON with a status per state and `Cache-Control: private, no-store, max-age=0`. Only `ready` carries text. Also here: the preview fixture (`fixtureRequestReads`) and its only gate (`requestSource`, the same `previewAllowed` as U1R's `creditSource`). |
| `.../[requestId]/request-view-model.ts` | `resultAccessOf(job)` is F2C.b `read_outcome` applied to the persisted fields. Available vs expired is the database's own `result_available`, which uses its clock and the persisted expiry. `requestDetail` / `requestDetailModel` cover the phase (waiting/running/finished), status, exact charge via U1R's `jobChargeView` (charge vs hold vs unknown, in the job's own unit), tokens, sanitized actionable failure copy per terminal cause, and a result note. `empty` means not found, and a foreign id reads the same as an unknown one. Pure browser rules: `pollDelayMs` (2 s doubling to a 30 s cap, 20 polls, about 8.5 min, then it stops), `clientResultRead` (a redirect or a 401 means signed out; body state and status must agree; `ready` needs text) and `expiryDelayMs` (due at the instant; capped at the setTimeout limit, then re-armed). `RETRY_GUIDANCE` states that a rerun is a new, separately charged request, and that the same Idempotency-Key + body + mode within 24 h answers with this request (R91, R94, P-25 idempotency 86,400 s). |
| `.../[requestId]/page.tsx` | Server page, `ƒ`. It renders the Status, Charge and Result cards, the retry guidance and the poller. The signed-out path redirects to `/login?next=`. It reads **metadata only**; content never enters the RSC payload. |
| `.../[requestId]/result/route.ts` | `GET` only, `force-dynamic`. Session → `result(id)` → `resultResponse`. Signed out → 401 JSON. No mutation verb exists. |
| `.../[requestId]/request-context.ts` | Glue: `requestSource(async () => { createClient(); getUser(); postgrestRequestReads(...) })`, and `null` when signed out. It has no gate of its own. |
| `.../[requestId]/result-panel.tsx` | Client component. It holds content in React state only. It fetches with `cache: "no-store"` and `credentials: "same-origin"`, aborts on unmount (navigation), re-reads on a back/forward-cache restore (`pageshow.persisted`), and drops content at the persisted expiry. Copy and Download (a Blob URL, revoked) appear only in `ready`. "Load the result again" re-reads and never resubmits. It has no storage, no analytics and no logging. |
| `.../[requestId]/status-poller.tsx` | Client component. It calls `router.refresh()` with the bounded backoff, stops on unmount, and shows "Stopped checking automatically. Check again" once the bound is reached. It is rendered only while the request is unfinished or its read hit a retryable outage. |
| `apps/app/tests/u/request-detail.test.ts` | 16 node cases (R01–R06, V01–V08, G01, S01). |
| `apps/app/tests/u/request_world.py`, `request-pg.test.ts` | Real-PostgreSQL oracle, 7 cases (P01–P07). |
| `apps/app/tests/u/run-mutants.mjs` | Extended (not rewritten): the U4 file constants, `request-detail.test.ts` added to the suite, and 25 U4 mutants. |

The detail is reached from U1R's existing links (`requestDetailHref` → `/usage/<id>` on every usage row and ledger debit). No navigation change is needed.

## Requirement coverage (brief §U4, manifest slices)

| Brief item / slice | Where | Tests |
|---|---|---|
| 1. Safe request ID/model/time/status; waiting/running/terminal phase; exact usage/charge state; sanitized actionable failure; owned output within persisted expiry, with copy/download only when authorized; no provider trace tree, no other tenants' media | view model, page, result panel | R01, V01–V04, P01, P02 |
| 2. Polling with bounded backoff and cancellation on navigation; auth loss, backend outage, not-ready, expired and unknown usage handled distinctly; retry never silently submits a paid inference; content expiry keeps permitted metadata | `pollDelayMs` + poller; `clientResultRead`; `RETRY_GUIDANCE`; result states | V05, V06, V08, R04, R05, P03, P06 |
| 3. Two tenants, shared/guessed links, role changes, expiry during an open page, changed retention config, reconnect and reload; no client-cache resurrection; private/no-store; no raw results in analytics or local storage | PG oracle; `resultResponse`; panel | P01, P03, P04, P05, R06, V07, S01 |
| Acceptance: UI and API agree on authoritative expiry and state | P01 compares the page with the API's own `get_owned_credit` + `read_outcome` at 3 store-clock instants | P01 |

Distinct states on the page:

- **Auth loss:** the page redirects to `/login?next=`. The result fetch reads "Your session ended. Sign in again" (a redirect, a 401, or a 42501/PGRST30x).
- **Outage:** the `ErrorPanel` shows "Try again" (re-read), and the poller keeps retrying with backoff. The panel shows "could not be loaded right now" with a re-read button.
- **Not ready:** "The result appears here when the request finishes", with polling.
- **Expired:** "expired at <UTC>; content removed; details and charge stay".
- **Unknown usage:** the charge card reads "Awaiting reconciliation" with the hold and no charge. The result is withheld (as the API withholds it).
- **No expiry recorded:** "cannot be shown" (pre-F2C.b success, R112).

## Tests first: fails-before record

| Suite / case | Before implementation | After |
|---|---|---|
| `tests/u/request-detail.test.ts` (commit `da9d7265`, before any U4 module) | `ERR_MODULE_NOT_FOUND .../usage/[requestId]/request-reads.ts`: the whole file fails (1/1 fail). Old behaviour: U1R's `/usage/<id>` links led to no route (404). | 16/16 |
| R04 auth-loss assertion (added before the fix in `6a47ee57`) | `U4-R04` fails: a 42501 job read gave `unavailable`, not `signed_out` (0/1) | 1/1 |
| V04 / G01 | V04's first version expected row instants and failed against the fixture instants (a test bug, corrected). G01 failed by exception under mutant M09 (object `assert.equal`) and was changed to assertion form; M09 is now killed. | pass |
| `request-pg.test.ts` P05 (first world run) | The role-change prelude (adding CONSUMER_2 as owner of CONSUMER_1's personal org) was refused by the database: `23514 ... the membership of a personal organization that funds a wallet is frozen`. The probe now asserts that refusal, then tests a real role change: both individuals become owners of one shared organization. | 7/7 |
| PG oracle discrimination (mutants in scratch copies of `apps/app`, whole world rerun) | **M01** (job read not narrowed) → P01, P02, P03, P04, P07 fail (5/7). **M04** (content gate removed) → P01, P07 fail. **M11** (ignores the DB's `result_available`) → P01, P03, P04 fail. | all 7 pass unmutated |

Every new case has a failure oracle, stated in the header of its file. The U runner proves each seam with a named kill.

## Real-database oracle

`request_world.py` reuses the D harness: a labelled, locked, self-removing container `infrx-app-u4-postgres` on 127.0.0.1:55456 (INFRX_D_TASK=app-u4), database `infrx_app-u4_request`, with the frozen DB clock. It uses the real `admit` / `claim_preparation` / `prepare` / `claim` / `put_result` / `terminalize` and writes no admission, settlement or result SQL. It builds these CONSUMER_1 requests:

- `kept`: success settled under result TTL 86,400 s.
- `short`: success settled after the TTL configuration was changed to 60 s.
- `unknown`: published, result stored, usage never reported → `held_unknown`.
- `failed`: `engine_error` → absorbed.
- `running`.
- `queued`.

It also builds `theirs`, a CONSUMER_2 success. It runs `assert_no_drift`. At store-clock +0 s, +120 s and +86,401 s it records the API's classification through `PgJobStore.get_owned_credit` + `read_outcome`, which is what `GET /v1/jobs/{handle}/result` uses:

```
{"0":     {"kept":"available","short":"available","unknown":"held_unknown","failed":"no_result","running":"pending","queued":"pending","theirs":"available"},
 "120":   {"kept":"available","short":"expired",  ...},
 "86401": {"kept":"expired",  "short":"expired",  ..., "theirs":"expired"}}
```

`request-pg.test.ts` then runs the App's unchanged `postgrestRequestReads` against a PostgREST double. A set-returning RPC is rendered as `json_agg` and a scalar RPC as `to_json`. Each call goes through `psql` as role `authenticated` with the JWT subject, or as `anon` with no subject.

- **P01:** at each instant, for every own request, page access equals the API's value, the result read state equals the API's, and polling happens only for running/queued. CONSUMER_2's own result follows the same rules.
- **P02:** an available result is exactly the stored `job_results.body`. The charge shown is `credits(−inference_debit)`. An absorbed failure shows no charge. A running request shows a hold and no charge.
- **P03:** the persisted `result_expires_at − settled_at` is 86,400 s for `kept` and 60 s for `short`, so the changed TTL moved only the later result. At +120 s, `short` is expired and `kept` is still available. At +86,401 s, `kept` is expired. Request id, created, model, revision, mode, status, charge and tokens are identical before and after.
- **P04:** on a page open across the expiry, the browser timer is armed at exactly 60,000 ms and is due at the instant. A reload reads `expired`. The route response is 410, `private, no-store, max-age=0`, with no `text`.
- **P05:** CONSUMER_2, a provider admin, an ungranted individual, and CONSUMER_2 after the role change all get `null` from job and `not_found` from result for all six requests. CONSUMER_1 cannot read `theirs`. A guessed UUID is not found. A direct `consumer_job_result(kept)` as CONSUMER_2 is refused `not_found`. A funded personal org refuses a new member (23514).
- **P06:** anon gets `forbidden` from job and `signed_out` from result.
- **P07:** the App answers `withheld` for the unknown-usage success, as the API does. **The recorded fact:** `consumer_job_result(unknown)` called directly returns the body (`# U4-P07 consumer_job_result(unknown-usage success) alone: returns the body (state succeeded, expiry set)`). See WR-U4-2.

## Commands (UTC 2026-09-25 22:05–22:49, worktree root unless noted)

| Command | Exit | Result |
|---|---|---|
| `git merge --no-ff codex/app-u1r` | 0 | clean merge, `bd6ff5d2` |
| `cd apps/app && pnpm install --frozen-lockfile` | 0 | lockfile unchanged |
| `make api-env` | 0 | pinned venv |
| `cd apps/app && pnpm test` (baseline after the merge) | 0 | 388 / 382 pass / 0 fail / 6 skipped |
| `node --test tests/u/request-detail.test.ts` (tests first, `da9d7265`) | 1 | ERR_MODULE_NOT_FOUND request-reads.ts (1 fail) |
| same, after `cb177069` | 0 | 16/16 |
| `cd apps/infrx-api && INFRX_D_TASK=app-u4 uv run --frozen python ../app/tests/u/request_world.py` (first run) | 1 | 6/7 (P05 prelude refused 23514, see above) |
| same, final tree | 0 | **7/7**, API reads as above |
| same world against scratch copies with mutants M01 / M04 / M11 | 1 / 1 / 1 | 2/7, 5/7, 4/7 pass (killed) |
| `make console-test` | 0 | `# tests 411 / pass 398 / fail 0 / skipped 13` (+23 cases; the 13 skipped are `credit-pg` (6) and `request-pg` (7), which need their world scripts and say so) |
| `make console-lint` | 0 | 0 errors, 2 warnings, both pre-existing (`lib/contracts/conformance.ts`, `lib/contracts/fake-services.ts`) |
| `make console-typecheck` | 0 | typegen + `tsc --noEmit` clean |
| `make console-mutants` | 0 | contracts self-test 14/14, contracts 212/212; V 40/40; **U 2/2 self-checks, 122/122 killed** (97 U1+U1R, +25 U4); C self-test 4/4, 104/104 |
| `node tests/u/run-mutants.mjs --only U4-M01..U4-M25` | 0 after the G01 fix | 25/25 killed (first run 24/25: M09 was a runner-error, fixed as above) |
| `cd apps/app && pnpm build` | 0 | `ƒ /usage/[requestId]`, `ƒ /usage/[requestId]/result` |
| `grep -rlE 'service_role\|SERVICE_ROLE\|CONSOLE_CURSOR_SECRET\|Demo result for request\|consumer_job_result\|b1000000-0000-4000-8000' .next/static` | — | 0 files |
| compiled server gate | — | `async function em(e,t=process.env){return!function(e=process.env){return null!==function(e=process.env){return null}(e)}(t)?e():{fixture}}`: constant real path |
| `docker ps -a \| grep -i u4` | — | 0 containers left |

Environment: Linux 7.0.0-1010-aws, Node 22, pnpm 9.15.9, Next 16.3.5, postgres 16.14 (D harness image). This lane touched no hosted Supabase project, pilot box, AWS/SSM or other lane's port. No dev server was started. Scratch copies live only in the session scratchpad.

## Wiring requests (not applied)

**WR-U4-1: run the App's real-PostgreSQL oracles from one target** (`Makefile`, coordinator). Today both world scripts are run by hand. Patch:

```make
# U1R/U4: the App's read adapters against real PostgreSQL as the browser principal, each on its
# own task-local instance (D harness). A missing Docker prints SKIP and exits 0, as tests/d does.
console-pg:
	cd $(API) && INFRX_D_TASK=app-u1r uv run --frozen python ../app/tests/u/credit_world.py
	cd $(API) && INFRX_D_TASK=app-u4 uv run --frozen python ../app/tests/u/request_world.py
```

Also add `console-pg` to the `.PHONY` list (and to `check` if the coordinator wants Docker there). Proof: U4's command above exits 0 with 7/7; U1R's evidence records 6/6.

**WR-U4-2: D10 SQL (only D10 writes SQL): `consumer_job_result` must refuse what the API withholds.** P07 shows that the RPC, which is granted to `authenticated` and callable directly with the public anon key and the user's JWT, returns the body of a success whose usage was never reported (`held_unknown`). `read_outcome` and the gateway withhold that result. A held_unknown hold is released as platform-absorbed after 24 h, so such a result can end up never charged. The App is already safe because it gates on the job read (R04, P07; mutant M04 is killed). The RPC itself is not. Patch for the next free migration:

```sql
create or replace function public.consumer_job_result(p_request_id uuid) returns text
language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
declare
  v_org uuid;
begin
  if auth.uid() is null then
    raise exception 'not signed in' using errcode = '42501';
  end if;
  v_org := public.consumer_org();
  if v_org is null or not exists (select 1 from infrx.jobs j where j.request_id = p_request_id
                                   and j.org_id = v_org and j.result_ref is not null) then
    perform infrx.refuse('not_found', 'no result for request ' || coalesce(p_request_id::text, ''));
  end if;
  -- F2C.b read_outcome: a success is served only on authoritative usage (not held_unknown).
  if exists (select 1 from infrx.jobs j where j.request_id = p_request_id
              and (j.settlement_state is not distinct from 'held_unknown'
                   or j.usage_prompt_tokens is null)) then
    perform infrx.refuse('result_pending', 'the result is not served while its usage is unreconciled');
  end if;
  return infrx.read_result(v_org, 'infrx-result:' || p_request_id);
end $$;
```

Proving test (U4-owned, lands with the migration). In `request-pg.test.ts` P07, replace the `console.log` line with:

```ts
  assert.equal(direct.error?.code, "P0001", "consumer_job_result served an unknown-usage result");
  assert.match(direct.error?.message ?? "", /^result_pending:/);
```

It fails on the current schema (the body is returned) and passes with the patch. The App-side mapping needs no change: `result_pending:` maps to `pending`.

**WR-U4-3 (optional, U1R module): expose `job_handle` and `settled_at` on `ConsumerJob`.** `consumer_jobs` already returns both columns, but `billing/credit-reads.ts` `jobOf` drops them. With them the detail could show "Finished at" and the job handle a client polls with (`GET /v1/jobs/{handle}`). Patch in `credit-reads.ts`: add `jobHandle: string; settledAt: string | null;` to `ConsumerJob`, and `jobHandle: text(row, "job_handle"), settledAt: optionalInstant(row, "settled_at"),` to `jobOf`. Add the two fields to `credit-fixture.ts` `job()`. U4 would then add two `Item`s to the Status card. Proof: `credit-reads.test.ts` rows already carry both columns, and one `assert.equal(job.jobHandle, "job_1")` pins it. Not required for acceptance.

**WR-U4-4: C0 overlap.** If C0's consumer query port subsumes `postgrestCreditReads`/`consumer_jobs`, only `usage/[requestId]/request-context.ts` changes. The reads take an injected client.

No mutant-list wiring is needed: `tests/u/run-mutants.mjs` is in the U path and already runs in `make console-mutants`.

## Proposed ruling (the coordinator numbers it)

"U4: the consumer request detail reads the request only through `consumer_jobs(p_request_id)` in the signed-in user's own session. It reads content through `consumer_job_result` only when F2C.b `read_outcome` over those persisted fields is `available`. Available vs expired is the database's `result_available`, never an App or browser clock. Content reaches the browser only from `GET /usage/<id>/result`, whose every answer is JSON with `Cache-Control: private, no-store, max-age=0`. The browser keeps content in memory only, re-reads it on a back/forward restore, and drops it at the persisted `result_expires_at`. A foreign, unknown or malformed id reads as not found. No control on the page submits inference; re-running is the client's own new, separately charged request."

## Unresolved inputs

- **P-25 (decided):** result TTL 86,400 s and idempotency 86,400 s. The page never states a TTL constant; it shows each request's persisted expiry. The retry copy's "24 hours" is the idempotency window.
- **P-26 (decided):** this lane does not touch key revocation copy (U2/A3).
- **Scrubbed content (0020):** scrubbing happens only after the persisted expiry, and `consumer_jobs.result_available` already excludes scrubbed rows, so the page shows `expired`. The PG world does not run a scrub (it is D10/M6's lifecycle).
- **Live headers:** the no-store header is proven on `resultResponse`, which is the only thing the route returns (R06, P04, mutants M07/M25). No live server was run against a real Supabase, which is coordinator-only (E3A/I2A).
- Base lacks D10-FOLLOWUP 0022; nothing here depends on it.

## Remaining effort

- Lane: 0 h, pending review.
- Coordinator/D10: WR-U4-1 about 0.1 h. WR-U4-2 about 0.5 h (one function plus the two P07 lines). WR-U4-3 about 0.5 h (optional).
- Estimate: optimistic 0.5 h, likely 1.5 h (one review/fix round), pessimistic 4 h. Confidence medium. Basis: every seam has a unit case, a named mutant kill and real-PostgreSQL agreement with the API's read path; what remains is review plus SQL and Makefile wiring whose patches and proofs are written.

## Fix round (2026-09-25 23:00–23:25Z; review of handback 313ee915)

Commits on `codex/app-u4` (nothing pushed, rebased, amended or stashed):

```
99395a20 U4 fix round: browser lifecycle regressions first (fail: drivers absent)
ee447470 U4 fix round: browser lifecycle as tested drivers (0-U4-V-01/02) ...
```

This evidence section and `updates/U4-20260925T2322Z.json` are committed on top of `ee447470`. The handback gives the final head.

### Findings

| Id | Status | What changed |
|---|---|---|
| 0-U4-V-01 (major) | **fixed** | The effect logic moved into `request-view-model.ts` as pure drivers with an injected timer, clock and event target. `watchExpiry(expiresAt, now, schedule, onExpire)` drops content at the expiry and never before it. It re-arms when a timer fires early (the 2^31−1 ceiling, or a slept laptop), drops at once content that arrives already expired, and unmount clears it. `watchResult(read, show, page)` reads on mount. On a *persisted* `pageshow` it first shows `loading`, so the old content is hidden, then reads again. Its cancel aborts the reads, and an answer that arrives after cancel is dropped. `readResult(id, signal, fetcher = fetch)` also moved here. It is tested with a fake fetcher for `cache: "no-store"`, `credentials: "same-origin"`, the signal, GET, and the id as one path segment; an abandoned read returns `null`. `result-panel.tsx` now only wires these, with no `fetch(` of its own, so the retry button also goes through `readResult`. New cases: V10, V11, V12, and S02 (source wiring with comments stripped). |
| 0-U4-V-02 (major) | **fixed** | `pollLoop(schedule, refresh, onStop)` refreshes on the backoff, calls `onStop` after exactly `MAX_POLLS` refreshes, then arms nothing; unmount cancels the next poll (V09). `pollsFor(model)` is the page's single mount decision, and T.poll now tests it directly. `status-poller.tsx` is one line, `useEffect(() => pollLoop(browserTimer, () => router.refresh(), () => setStopped(true)), [router, round])`. `page.tsx` renders `{pollsFor(model) ? <StatusPoller /> : null}`, and S02 pins that it has exactly one `<StatusPoller`. |
| 0-U4-V-03 (major) | **not fixed in lane: outside owned paths (D10 SQL)**. WR-U4-2 refined and proven | The regression now exists and is strict. P07 keeps the App/API agreement. The old `console.log` became **U4-P08**: the direct RPC for the unknown-usage success must be `P0001 result_pending:`, and the settled sibling is still served. P08 carries `todo: WR_U4_2` until D10 applies the patch. Real PostgreSQL on the current schema gives `not ok 8 ... # TODO` with `consumer_job_result served an unknown-usage result`. The same world with the WR-U4-2 SQL applied after the migrations gives `ok 8`. |

### Reproduced before, killed after

The review's surviving mutants were reproduced on the handback tree, one scratch copy each, running `node --test tests/u/request-detail.test.ts`. **A** (no drop at expiry), **B** (no re-read on persisted pageshow), **F** (no `controller.abort()`), **K** (retry via plain `fetch`), **E** (poller never stops) and **D** (page always polls) all gave `exit 0, # pass 16 # fail 0`, so each survived.

The regressions-first commit `99395a20` fails as a whole file: `SyntaxError: ... does not provide an export named 'pollLoop'`. After `ee447470` the file passes 21/21.

18 new U-runner mutants (U4-M26..M43) cover each review mutant in both its driver form and its wiring form, plus neighbours. All 18 are killed by their declared case:

| Mutant | Review mutant | What it breaks | Killed by |
|---|---|---|---|
| M26 | A | the driver never calls `onExpire` | V10 |
| M27 | A | the panel's `onExpire` is `() => {}` | S02 |
| M28 | B | the persisted check is `if (false)` | V11 |
| M29 | B | listens on a non-window target | S02 |
| M30 | F | `controller.abort()` deleted | V11 |
| M31 | F | the panel discards the driver's cleanup (`void watchResult`) | S02 |
| M32 | K | retry via plain `fetch` | S02 |
| M33 | E | `pollDelayMs(attempt) ?? FIRST_POLL_MS` | V09 |
| M34 | E | `onStop` is `() => {}` | S02 |
| M35 | D | the page renders `{true ? <StatusPoller />` | S02 |
| M36 | D | `pollsFor` returns `true` for non-ready | V05 |
| M37 | — | the poll loop ignores unmount | V09 |
| M38 | — | an early timer drops content | V10 |
| M39 | — | the expiry timer ignores unmount | V10 |
| M40 | — | a late answer is shown after navigation | V11 |
| M41 | — | a restore keeps the old content on screen | V11 |
| M42 | — | `credentials: "include"` | V12 |
| M43 | — | an abandoned read shows `unavailable` | V12 |

M23 (no-store) now targets `readResult` in the view model and is killed by V12. The fetch moved there, so S01 no longer greps the panel for it.

One problem with the runner itself: M33 was first classed a runner-error. The V09 `deepEqual` diff printed a bare `...` line, which the runner reads as the end of the diagnostic. The count assertions were moved ahead of the `deepEqual`, and M33 is now killed by assertion.

P04 now runs `watchExpiry` on the **store clock**, with its timer fired by hand:

- It is armed at 60,000 ms.
- Fired at +59 s, it does not drop the content and re-arms for 1,000 ms.
- Fired at +60 s, it drops the content.

### Commands (worktree root unless noted)

| Command | Exit | Result |
|---|---|---|
| `node --test tests/u/request-detail.test.ts` at `99395a20` | 1 | 0/1: missing export `pollLoop` (fails before) |
| same at `ee447470` | 0 | 21/21 |
| `cd apps/app && pnpm test` | 0 | `# tests 417 / pass 403 / fail 0 / skipped 14 / todo 0`. That is +5 node cases (V09–V12, S02) and +1 PG case (P08). The 14 skipped are credit-pg 6 + request-pg 8, which need their world scripts. |
| `pnpm lint` | 0 | 0 errors, 2 pre-existing warnings (`lib/contracts/conformance.ts`, `fake-services.ts`) |
| `pnpm exec next typegen && pnpm exec tsc --noEmit` | 0 | clean |
| `node tests/contracts/run-mutants.mjs --self-test && pnpm test:mutants` | 0 / 0 | 14/14 self-tests; 212/212 killed |
| `node tests/v/run-mutants.mjs` | 0 | 40/40 killed |
| `node tests/u/run-mutants.mjs` | 0 | 2/2 self-checks; **140/140 killed** (122 + 18) |
| `node tests/c/run-mutants.mjs --self-test && node tests/c/run-mutants.mjs` | 0 / 0 | 4/4; 104/104 |
| `cd apps/app && pnpm build` | 0 | `ƒ /usage/[requestId]`, `ƒ /usage/[requestId]/result`. 0 files in `.next/static` match service_role / SERVICE_ROLE / CONSOLE_CURSOR_SECRET / `Demo result for request` / `consumer_job_result` / fixture ids. |
| `cd apps/infrx-api && INFRX_D_TASK=app-u4 uv run --frozen python ../app/tests/u/request_world.py` | 0 | 7 pass + 1 todo. `not ok 8 # TODO`, with the error `consumer_job_result served an unknown-usage result` |
| same world with the WR-U4-2 SQL applied after the migrations (scratchpad wrapper that wraps `pgharness.apply`; task-local DB only) | 0 | 7 pass + `ok 8 # TODO`. The patch makes P08 pass, and P01–P07 are unchanged. |
| `docker ps -a \| grep u4` | — | 0 containers left |

**Isolation incident (not caused by this lane):** from 23:04Z to about 23:22Z a container named `infrx-q3-valkey-55456`, which is not ours, was bound to **127.0.0.1:55456**. That port is app-u4's reserved Postgres port; `tasklocal.py` gives q3 55462. The lane did not touch it and waited until it was released before running the world. The coordinator may want to find which Q3 run used 55456.

### WR-U4-2 (revised proof; the patch itself is unchanged from the section above)

- Migration: the next free D10 number. That is `0024` if D10-APP-SQL (`codex/d10-app-sql`) can still take it; otherwise `0025`.
- The SQL is exactly the `create or replace function public.consumer_job_result` shown under WR-U4-2 above. It was applied verbatim in the proof run.
- The proving test is already in the tree: `tests/u/request-pg.test.ts` **U4-P08**. When the migration lands, it needs one edit: delete `todo: WR_U4_2` from P08's options, along with the `WR_U4_2` constant. The composed result is then `ok 8` with no TODO, and exit 0 (proven above).
- The App-side mapping does not change: `result_pending:` maps to `pending`.

### Remaining effort

- Lane: 0 h, pending recheck.
- D10 WR-U4-2: about 0.5 h, one function plus a one-line test edit.
- WR-U4-1, WR-U4-3 and WR-U4-4 are unchanged.
- Estimate: optimistic 0.25 h, likely 0.75 h, pessimistic 2 h. Confidence medium-high. Basis: both browser findings are closed, with named kills for every review mutant in driver and wiring form. The remaining finding is SQL outside the lane, and its patch is proven on real PostgreSQL.
