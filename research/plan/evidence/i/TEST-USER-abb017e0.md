# TEST-USER — internal test users without email verification (abb017e0)

Base `17cc78bd`, head `abb017e0` (code; evidence committed on top), branch `codex/test-user`,
worktree `.claude/worktrees/codex-test-user`. User decision 2026-09-27: v1 is tested internally
with no real users; email verification is skipped, test users are created directly in Supabase
by script. Nothing hosted, AWS, SSM, Vercel or the box was read or changed. No migration, no
product code (`apps/infrx-api/infrx/`, `apps/app/`) touched.

## Changed paths

- `infra/app/create-test-user.py` (new, stdlib only; any python3.12)
- `infra/app/operations.md` (section "Test users without email verification"; verification-log line)
- `tests/integration/ops/test_create_test_user.py` (new, 13 cases / 18 items)
- `tests/integration/mutants.py` (TU_TOOL/TU_SUITE, control `tuc01`, mutants `tum01`–`tum18`)

## What the App does (read before building) and what the tool does

- Grant: `app/(auth)/grant.ts` `claimSignupGrant(userId)` → service-role `.rpc("claim_signup_grant",
  {p_user_id, p_campaign_version: "consumer-v1"})` (flow.ts `CLAIM_RPC`/`claimArgs`/`SIGNUP_CAMPAIGN`),
  called by `/auth/callback` and `/welcome` after a verified session. Not a DB trigger: 0001's
  `handle_new_user` only makes the profile and personal org. `claim_signup_grant` (0015) derives
  verification itself from `auth.users.email_confirmed_at` (0009 `infrx.verified_user`), is gated
  by `infrx.require_feature('signup_grant')` (0021: `maintenance: signup_grant is not enabled`,
  SQLSTATE 55000) and is idempotent per individual and per address (R71, R85).
- The App's consumer gate: `resolveConsumerContext` refuses a user without `email_confirmed_at`.
- The tool: GoTrue admin `POST /auth/v1/admin/users {email, password, email_confirm: true}`;
  422/409 → `GET ?filter=<email>` (exact, case-insensitive pick) → `PUT /admin/users/<id>
  {email_confirm: true, password}`; refuses (exit 3) when the answer has no `email_confirmed_at`;
  then the same RPC and arguments as grant.ts; then `console_wallet_summary(p_user)` (0008) as
  /welcome reads it. No table write; CREDIT is echoed as the DB's exact decimal string.
- Output: user_id, email, confirmed, created, grant (granted | already-granted | flag-off |
  unavailable), grant_detail, wallet_id, available, unit. Exit 0 / 2 (bad input or env, nothing
  called) / 3 (refused or failed call; a held grant). 5 s timeout per call, redirects never
  followed, plain http only for localhost; errors show step + status + reply code only.

## Commands (all in the worktree)

| Command | Exit | Result |
|---|---|---|
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/ops/test_create_test_user.py` before the tool existed | 1 | **15 failed, 1 skipped** (fails-before; tu12/tu13 added later, killed by tum15/tum16) |
| same, after | 0 | 17 passed, 1 skipped (tu11 needs app-c0) |
| `INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase … pytest -q tests/integration/ops/test_create_test_user.py` | 0 | **18 passed** (tu11 on the real Supabase image + pinned PostgREST v13.0.4, 55451) |
| tu11 with tum18 applied by hand (RPC path renamed), app-c0 | 1 | 1 failed (layer-2 kill proven; the runner reports tum18 `pending` without E2's live stack) |
| `apps/infrx-api/.venv/bin/python tests/integration/mutants.py --layer all --only <id>` for each of tuc01, tum01–tum18 | 0 | control SURVIVED; tum01–tum17 killed alone; tum18 pending |
| the 19 as one list (`summarise(run_one(...))`) | — | mutants 19, killed 17, controls_survived 1, not_killed 0, pending 1, problems None |
| `pytest -q tests/integration/test_run.py -k "anchor or mutation_stage_runs_every_list"` | 0 | 2 passed (anchors occur as declared; every path in a copied tree) |
| `pytest -q tests/integration/ops` | 0 | 51 passed, 1 skipped (test_i3_operations link/anchor checks green with the new section) |
| `pytest -q tests/integration/backend/recovery/test_mutant_list_i3b.py` | 0 | 1 passed |
| `ruff check infra/app tests/integration/mutants.py tests/integration/ops/test_create_test_user.py` | 0 | clean |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |
| `git diff --stat 17cc78bd..abb017e0` | 0 | the four owned paths only |

Mutant first kill found a weak decision: `tum16` (drop the wallet-read status check) SURVIVED
because a PostgREST error is never a list; the redundant status check was removed and the
mutant re-aimed at "a failed wallet read prints a zero" (killed by tu13).

## Cases and their oracles

tu01 new user: create body has `email_confirm: true` + password, claim body = grant.ts's args,
wallet read, apikey + Bearer on every call, exact output (tum01, tum05) · tu02 existing
(upper-case) address among a substring decoy: confirm + password PUT, rerun reads
already-granted, balance 10000 not 20000 (tum02, tum03, tum06) · tu03 flag off → `flag-off`,
exit 0 (tum07) · tu04 401 → exit 3, one request, no body echo (tum09, tum10) · tu05 dry run →
no request (tum13, tum17) · tu06 no key/password in stdout/stderr on success, dry run, text,
JSON, held (tum17) · tu07 bad email / empty or missing password var / key / URL / plain-http
remote → exit 2, no request (tum12, tum14) · tu08 302 not followed (tum11) · tu09 unconfirmed
answer → exit 3 before any RPC (tum04) · tu10 `identity_reused` → unavailable, exit 3 (tum08) ·
tu12 a 7 s stall gives up under 6.5 s (tum15) · tu13 wallet read 500 → exit 3, never a zero
(tum16) · tu11 (layer 2, app-c0) a synthetic confirmed `auth.users` row, the fake answers
/auth/v1 and forwards /rest/v1 to the real PostgREST with a service_role JWT: granted then
already-granted, 10000.00000000 both times, exactly one ledger row (tum18).

## Wiring requests

None required. Optional (coordinator): `make check` does not run `tests/integration/ops`; the
existing I3 ops tests are in the same position. Nothing is added to the Makefile here.

## Open issues

- [OP] First hosted run: the coordinator exports the two SSM values (operations.md), runs
  `--dry-run`, then the real run; ⚠️ the admin API ignoring the public-signup switch is to be
  verified on that run. The hosted `signup_grant` flag state is still ⚠️ TO BE VERIFIED
  (operations.md cutover note); `flag-off` says so without failing.
- Revocation is documented (ban via GoTrue admin PUT, `revoke-key`, R85 retire for deletion);
  no revoke tool was added (YAGNI for internal testing).

## Remaining effort

Lane: 0 h (review only). Hosted use: optimistic 0.1 h, likely 0.25 h, pessimistic 1 h
(confidence medium; basis: two exports + one command; pessimistic if the hosted flag is off or
GoTrue answers a different existing-user code).

## Fix round (review 0-TU-R1, 0-TU-R2) — code head `8cc9c8fb`

Handback head was `92aaa6c7`. Changed: `infra/app/create-test-user.py`, `infra/app/operations.md`
(same section + one verification-log line), `tests/integration/ops/test_create_test_user.py`,
`tests/integration/mutants.py`.

- **0-TU-R1 (blocking) fixed.** `main()` refuses a key that is not printable ASCII
  (`re.fullmatch(r"[\x21-\x7e]+", key)`) with exit 2, `SUPABASE_SERVICE_ROLE_KEY has characters
  a header cannot carry`, before any call and without echoing it. `call()` now catches
  `(OSError, http.client.HTTPException, ValueError)` and still prints only the exception's type
  name, so a rejected header or a non-HTTP answer is a refusal (exit 3), never a traceback.
  Reviewer's reproduction (`SUPABASE_SERVICE_ROLE_KEY=$'placeholder-SECRETKEY\r'`) now prints
  only that sentence, exit 2. operations.md says so and says to re-store the parameter.
- **0-TU-R2 (major) fixed.** An address that exists (the create answers 409/422 and the lookup
  finds exactly that address) is refused with exit 3, `address exists; pass --reset-existing to
  confirm and re-password it`, after one read-only GET; no PUT, no grant. `--reset-existing`
  opts in to the old confirm + re-password repair. `--dry-run` shows which path it would take.
  operations.md documents the flag and its risk (it locks a real owner out and marks an
  unverified address verified; use only on an operator-owned address). Skipped:
  `--allow-domain` (optional in the finding; the opt-in flag already closes the silent path).

| Command | Exit | Result |
|---|---|---|
| `pytest -q tests/integration/ops/test_create_test_user.py` with the new cases, before the fix | 1 | **6 failed**, 15 passed, 1 skipped: tu07[key-cr] and [key-lf] exit 1 (traceback), tu14 exit 0 (re-passworded), tu15 exit 1 (BadStatusLine traceback), tu02/tu06 (no `--reset-existing` yet) |
| same, after | 0 | 21 passed, 1 skipped |
| `INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase pytest -q tests/integration/ops/test_create_test_user.py` | 0 | **22 passed** (tu11 on the real Supabase image, now with `--reset-existing` for its pre-existing row) |
| tu11 with tum18 applied by hand, app-c0, then `git checkout --` the file | 1 | 1 failed (layer-2 kill re-proven) |
| `tests/integration/mutants.py --layer all --only <id>` for tuc01, tum01–tum21 | 0 each | control SURVIVED; tum01–tum17 and tum19–tum21 killed; tum18 pending (layer 2) |
| `pytest -q tests/integration/ops` | 0 | 55 passed, 1 skipped |
| `pytest -q tests/integration/test_run.py -k "anchor or mutation_stage_runs_every_list"` | 0 | 2 passed |
| `pytest -q tests/integration/backend/recovery/test_mutant_list_i3b.py` | 0 | 1 passed |
| `ruff check` (owned Python) | 0 | clean |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

New cases and mutants: tu07[key-cr], tu07[key-lf] (a key ending in CR / LF → exit 2, no request,
no secret; tum19 removes the check) · tu14 an existing address without `--reset-existing` →
exit 3, requests = POST + GET only, password still `old`, not confirmed, no grant (tum21 removes
the refusal) · tu15 a non-HTTP answer → exit 3 naming the step, no traceback, no secret (tum20
narrows the except back to OSError). tu02, tu06 and tu11 now pass `--reset-existing`.

Remaining effort: lane 0 h (review only); hosted use unchanged (0.1 / 0.25 / 1 h, medium).
