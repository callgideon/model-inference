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
