# C3F — authorized feedback and review actions — evidence at c84aa8c

Lane `feedback-c3f` (wave LW2), branch `codex/w5-feedback-c3f`, worktree `.claude/worktrees/codex-w5-feedback-c3f`.
Base `eb0734d7` (L2 merged: `infrx/lab/access`, 0027/0030; D6F merged: 0028). Head `c84aa8cc`. Tasklocal key `app-c3f`
(postgres 57509, container `infrx-app-c3f-postgres-supabase`, db `infrx_app_c3f_c3f`, PostgREST `infrx-app-c3f-postgrest`,
network `infrx-app-c3f-net`; all removed at exit). G4F is not redone (codex/w5-feedback aad160c6, coordinator merging).

## Changed paths (owned only)
- `apps/app/lib/services/feedback.ts` (new): `submitOwnFeedback(context, client, input)` + `SUBMIT_FEEDBACK_RPC`.
- `apps/app/tests/c/feedback/` (new): `feedback.test.ts` (A01–A06), `feedback-postgrest.test.ts` (P01–P02, skips without the stack),
  `run-mutants.mjs`, `stack.py` (real PG + PostgREST + SQL mutants), `proposed_doors.sql` (WR-C3F-1 text).
- `apps/lab/lib/services/review/` (new): `index.ts` `reviewFeedback(client, workspace, requestId)`; `actions.ts` "use server"
  `reviewRequestFeedback` (guard first: `requireProviderWorkspace`).
- `apps/lab/tests/c/review/` (new): `review.test.ts` (L01–L04), `review-postgrest.test.ts` (P03–P04), `run-mutants.mjs`.
Nothing in `app/actions.ts`, `lib/services/actions.ts`, `console.ts`, nav/layout, Makefile, migrations, lockfiles: the launched App is unchanged
(the action is composed by nobody until WR-C3F-2; the door does not exist hosted; 0028's `feedback` flag is off by default).

## Behaviour
- **App (consumer audience, narrow own-feedback):** allowlisted `FeedbackInput` only (a smuggled author/channel/org/calibration/operator field
  is `invalid_request` before any call); a ready session only; one call `rpc("submit_feedback", {p_args:{request_id,name,value,comment,idempotency_key}})`
  on the individual's OWN client. The door derives the org (the job's, only if the caller is a member: another org's or unknown request =
  `not_found`), author = `auth.uid()`, channel `console`, role `customer`, `by_operator = is_operator()` (R41 marker), idempotency operation
  `feedback.submit` and the digest, then calls 0028's `accept_feedback` (flag, suspension, replay/conflict, outbox). Refusals keep their code with
  fixed text; 42501 = forbidden; anything else (0A000 flag off, missing door, unknown code, transport) = `dependency_unavailable`; an ack only for a
  stored `customer`/`console`/non-calibration row, else `internal_error` (a label can never come back as this action's answer).
- **Lab (provider audience, review):** guard first; the selected workspace + request id to `lab_review_feedback` on the user's own session (no
  identity, no service key). The door applies L2's rule on the DB clock, as `LabAccess.authorize_content`: a current membership (else `not_found`),
  developer/administrator (else `forbidden`), AND the grantor's CURRENT grant version to that provider naming the job's model, `feedback` and
  `provider_sharing`, not revoked/expired (else `not_found`: forged/foreign/unknown/revoked are one answer). Rows = customer (or judge) signals,
  8 fixed columns, never a label, never the customer's principal/org/marker; any other row fails the whole review closed.
- No action is imported across apps (the Lab defines its own DTO; L1-B04 boundary case still passes).

## Seam tests first (check 1)
- `cd apps/app && node --test tests/c/feedback/feedback.test.ts` before `lib/services/feedback.ts` existed → exit 1, `ERR_MODULE_NOT_FOUND ... lib/services/feedback.ts`.
- `cd apps/lab && node --test tests/c/review/review.test.ts` before `lib/services/review/index.ts` existed → exit 1, `ERR_MODULE_NOT_FOUND ... review/index.ts`.
- Failed-then-passed during the lane: `make console-test` exit 2 (1 fail: `client-boundary` "every case asserts success through a helper" flagged a bare
  `assert.ok(!result.ok)` in A04) → message added → exit 0.

## Commands (exit codes, counts)
| # | Command | Exit | Result |
|---|---|---|---|
| 2 | `cd apps/app && node --test tests/c/feedback/feedback.test.ts` | 0 | 6/6 |
| 2 | `cd apps/lab && node --test tests/c/review/review.test.ts` | 0 | 4/4 |
| 3 | `make console-test` | 0 | 679 tests: 620 pass, 0 fail, 59 skipped (incl. P01–P02 visibly without the stack) |
| 3 | `make console-typecheck` | 0 | typegen + tsc clean |
| 3 | `make console-lint` | 0 | 0 errors, 2 pre-existing warnings (not in C3F files) |
| 3 | `cd apps/app && pnpm build` | 0 | App builds |
| 3 | `make lab-test` | 0 | 33 tests: 31 pass, 2 skipped (P03–P04 without the stack) |
| 3 | `make lab-lint` / `make lab-typecheck` / `make lab-build` | 0/0/0 | clean; Lab builds (ƒ /) |
| 3 | `make lab-mutants` (L1's list, unchanged) | 0 | 27 cases named; 50/50 killed |
| 4 | `cd apps/app && node tests/c/feedback/run-mutants.mjs` | 0 | 6 cases all named; 18/18 killed |
| 4 | `cd apps/lab && node tests/c/review/run-mutants.mjs` | 0 | 4 C3F cases all named (+ L1-B01 for the action's guard); 20/20 killed |
| 5 | `cd apps/infrx-api && INFRX_D_TASK=app-c3f INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/feedback/stack.py` | 0 | 6/6 PG checks + App P01–P02 2/2 + Lab P03–P04 2/2 via PostgREST v13.0.4 |
| 4/5 | same + `--mutants` | 0 | 22/22 SQL mutants of the doors killed; every PG check named |
| – | `uv run --frozen ruff check ../app/tests/c/feedback/stack.py` | 0 | clean |

## Real-PG evidence (app-c3f, 57509; the 0028/0030 role matrix)
- DUR-RLS: anon 42501 on both doors; grantees exactly {postgres, authenticated, service_role}; the platform role (no `auth.uid()`) `not_found` on both.
- FEEDBACK-ACK (App audience): C1 on own job → one `infrx.feedback` row (author C1, customer, console, by_operator false, calibration false), one
  `idempotency` row (`feedback.submit`), one `feedback_projection` outbox event; same key+signal → same row, counts unchanged; changed signal → `idempotency_conflict`.
- Forged request id from another org (C1 on C2's job), unknown and malformed ids → `not_found`; 5 smuggled provenance fields + a `calibration_label`
  name → `invalid_request`; nothing written. An operator member submitting is still `customer` with `by_operator` true.
- LAB-ACCESS (Lab audience): 36 cases (NEMO dev/admin/viewer, OTHER dev = CONSUMER_2, consumer C1, a revoked NEMO developer × NEMO/OTHER × job_1/job_2/unknown):
  door answer == `LabAccess.authorize_content` (PgAccessStore) in every case; allowed only NEMO dev+admin on job_1; viewer `forbidden`; everything else `not_found`.
  Rows: customer only, 8 columns, no label (the seeded operator label never appears).
- Revoked sharing: C2's revoked grant denies; a new C1 version without `provider_sharing`, without `feedback`, or past `expires_at` (DB clock advanced) denies;
  C1's committed revocation denies the very next review, and the port agrees.
- Both adapters through supabase-js + PostgREST: P01 ack/replay/conflict, P02 forged `not_found` + anon `forbidden`; P03 dev/admin review (api+console signals),
  P04 viewer forbidden, revoked/other-provider/forged-workspace/consumer/unknown `not_found`.
- 0028's flag-off refusal (0A000) is D6F's check (`tests/d/test_d6f_feedback.py`); the door has no path around `accept_feedback`. Measured: an explicit
  `revoke ... from public, anon` on the doors changes nothing (0004 default privileges), so none is written (a mutant of it survived → removed).

## Wiring requests
- **WR-C3F-1 (lab-sql; migration, local-only R151, number at merge):** add `apps/app/tests/c/feedback/proposed_doors.sql` as the next migration
  (`public.submit_feedback(jsonb)`, `public.lab_review_feedback(jsonb)`, `grant execute ... to authenticated`). Strictly additive; ROLLBACK in its header.
  `stack.py` uses the migration automatically once it exists (it applies the proposal only when the doors are absent). Proof: the table above.
- **WR-C3F-2 (coordinator; App composition):** `research/plan/evidence/c/C3F-c84aa8c-wiring.patch` (`git apply --check -p1` clean on c84aa8c):
  `ActionDeps.feedback?` + `consoleActions().submitFeedback` (Origin check, context, own client, `/traces` refreshed only after an ack, no client =
  `dependency_unavailable`); `app/actions.ts` passes the cookie client and exports `submitFeedback`; adds `tests/c/feedback/compose.test.ts`
  (C01–C02) and 3 composition mutants to the runner. Proven in-tree then reverted: tsc clean, compose + C3A actions tests 20/20, runner 8 cases / 21/21 killed.
  No page calls it (navigation untouched); a trace-detail control is U/V work.
- **WR-C3F-3 (Makefile):** `console-mutants` += `cd apps/app && node tests/c/feedback/run-mutants.mjs`; `lab-mutants` += `cd apps/lab && node tests/c/review/run-mutants.mjs`;
  new `console-c3f-real:` `cd $(API) && INFRX_D_TASK=app-c3f INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/feedback/stack.py && ... stack.py --mutants` (Docker; fails visibly without it).

## Open issues / deviations
- Integration is claimed only against real merged D6F (0028) + L2 (0027/0030/`LabAccess`); the two doors are this lane's proposal until WR-C3F-1 merges.
- Deviation: the permission rule for the Lab review lives in the door's SQL (a second statement of L2's predicate, proven equal to the port over 36 cases), because
  the Lab holds no service key and the TS predicate must not authorize (packages/shared/contracts/lab). Upgrade: a Lab server route over `LabAccess` if one is wired.
- Console idempotency scope is `feedback.submit` (API's is `feedback`): the same key string on both channels is two submissions, not a conflict.
- The App test builds a ready `ConsumerContext` by hand; C0's `resolveConsumerContext` composition is C3A's (unchanged) and the door ignores it (identity = the JWT).
- Proposed ruling (not numbered): "Console feedback is authored by `auth.uid()` on channel `console` under idempotency operation `feedback.submit`; a Lab
  provider reads another org's feedback only through a door that re-checks the current membership and the grantor's current grant (feedback + provider_sharing +
  the job's model) on the database clock, answering `not_found` for every denial except a member's missing capability (`forbidden`)."

## Estimate (remaining: one review round + wiring)
optimistic 1 h, likely 2 h, pessimistic 5 h; confidence medium; basis: C3A/G4F review history (one fix round each), all mutants and the real-PG stack green, WR-C3F-1 is a copy of a proven file.
