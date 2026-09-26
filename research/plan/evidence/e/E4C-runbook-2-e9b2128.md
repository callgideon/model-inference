# E4C-RUNBOOK-2 — window runbook corrections for E4C (lane evidence)

- Lane: E4C-RUNBOOK-2 (task E4C, I8/E track support), branch `codex/e4c-runbook-2`, worktree `.claude/worktrees/codex-e4c-runbook-2`.
- Base `6cbb6a45`; implementation head `e9b21287` (this file and the coordinator update are committed after it).
- Brief: `research/plan/evidence/coordinator/E4C-readiness-2026-09-26.md` (on the tip `dc1ec384`, not on the base) §2d-h, §5 (2).
- Nothing ran against the box, AWS, SSM or hosted Supabase. The only database used was a throwaway task-local PostgreSQL 16 container (`infrx-e1c-rb2-postgres`, 127.0.0.1:55449), removed afterwards.

## Changed paths (all owned)

| Path | Change |
|---|---|
| `infra/runbooks/rollout.md` | W6 seed from hosted's own `plan`; W6 pass line; W7 expectations 0001-0025; §1 regime row + `INSTALL_ARGS`; W7f; W10b; Known-good record `--set` names; log |
| `models/marlin2b/results/E4C-runbook.md` | §0 keeps 0.1/0.2; new §1a H1-H6 (old 0.3-0.8) after W7, before W8; §1 steps 1-2; §4 launcher; references renumbered; log |
| `infra/rollout/steps/55-runtime-login.sh` | new step |
| `infra/rollout/e4c-certify.sh` | new: a committed copy of the launcher (see defect 4) |
| `infra/rollout/README.md` | step row 8b; log |
| `tests/integration/backend/recovery/test_runbooks.py` | rb09, rb10, rb11 |
| `apps/infrx-api/tests/i/test_rollout.py` | step list + `e4c-certify.sh` in the strict-bash/no-secret case; the launcher case |
| `apps/infrx-api/tests/i/test_ops_steps.py` | the step-55 case |
| `apps/infrx-api/tests/i/mutants.py` | 8 mutants for the two new cases; the mutant layout copies `certify.py` and the E4C runbook |

The session-03 launcher `research/plan/evidence/coordinator/session-03-tools/rollout/e4b-certify3.sh` is **unchanged** (it is run3's record). The launcher now lives at `infra/rollout/e4c-certify.sh`.

## Defect → change → test

| # | Defect | Change | Test (fails before → passes) |
|---|---|---|---|
| 1 | RO W6 seeded the copy's history with the literal `('0001','init'),('0002','seed_models')`; hosted is at 0001-0018 (`20-platform-handoff-2026-09-24.md:73`), so the copy would plan 0003-0025, hosted 0019-0025, and W7's digest check aborts | W6 reads `HOSTED_APPLIED` from hosted's own read-only `migrate.py plan` `applied:` line at the window and derives `SEED` from it; the pass line requires `0001 init, …, 0018 terminal_settlement` and names hosted's flags at 0018 (`signup_grant` t); W7 comments expect `applied == $HOSTED_APPLIED` before and 0001-0025 after; a paragraph says the page is written for a tree with 0024/0025 (from `codex/d10-merge-2`) | `test_e4c_rb09_the_copy_is_seeded_with_hosted_s_own_applied_history`: FAILED on the base ("the stale seed"); runs the runbook's own `SEED=` line on the `applied:` line `migrate.describe` prints for a 0001-0018 history and gets exactly those 18 rows |
| 2 | RO:50 left `ACCOUNTING_REGIME` unset (legacy_usd); P-17 check 3 needs CREDIT with the P-01 card | `INSTALL_ARGS` `INFRX_SET` adds `ACCOUNTING_REGIME=credit ACTIVE_RATE_CARD_VERSION=rc_marlin2b_20260925_launch`; the §1 row states the order (card published and CREDIT activated before W10) and what the code does otherwise, each traced: no card → `validate_runtime` refuses (`infrx/config.py:249-251`), 50-install exit 2; card not active on hosted → `price_source` false (`routes/models.py:187-208`), pilot refuses to start (`routes/ingress.py:106-114`), exit 4; flags off → `require_feature` 55000 → 503 `dependency_unavailable` (`state/jobstore.py:116-117`); legacy note kept as history. W7f added. The Known-good record passes both names | `test_e4c_rb10_the_candidate_installs_the_credit_regime_with_the_p01_card`: FAILED on the base (INFRX_SET without the regime); also pins the card to the one E4C-runbook publishes and every INFRX_SET name into the known-good `--set` list |
| 3 | RB 0.3/0.4 (publish-card, dry-run, activation) were "before the window", but activation calls 0022's `infrx.set_feature_flag` (`infrx/operations/transition.py:206`) and hosted is at 0018 | §1a "After the hosted apply (before W8)" holds H1-H6 (old 0.3-0.8, same order); H2 carries the full activation command; §1 is now W1-W7 → §1a → W8-W13 (+W10b); every reference renumbered (§1 canary, §4 x2, §5.0 x2, §7, P-17 row 3) | `test_e4c_rb11_the_card_and_the_activation_follow_the_hosted_apply`: FAILED on the base; asserts neither command is in §0, the order H1..H6, W1-W7 < §1a < W8-W13, and no stale step reference (the regex finds 18 on the base text, 0 now) |
| 4 | The launcher (`e4b-certify3.sh:14-30`) lacked the three E4C flags and put the DSN on docker's argv (`-e DATABASE_URL="$DB6543"`, visible in `ps`) | `infra/rollout/e4c-certify.sh`: the same run plus `--run-profile /e4b/e4c/E4C-box.json --key-inventory /e4b/e4c/keys-certify.json --overload-profile /e4b/e4c/E4C-edge.json` (RB §3 paths through the existing `/e4b` mount); the pre-checks run in the script (names only, exit 2); the env-file rewrite of `DATABASE_URL` to :6543 is kept; every value goes into a 0600 `--env-file` removed on exit | `test_e4c_certify__the_launcher_passes_exactly_certify_s_box_flags_and_no_secret`: the launcher's flags == certify.py's `add_argument` flags minus `--scale/--keep/--hashes` == the RB §4 command's flags. Before: the old launcher misses `--key-inventory`, `--overload-profile`, `--run-profile` and matches the `-e NAME=value` secret shape (checked with the same extraction; the case itself FAILED on the base: no file) |
| 5 | No committed step set the `infrx_runtime`/`infrx_monitor` passwords or named their SSM parameters (`D10-HANDOFF-31c2112.md:138`, wiring 6) | `infra/rollout/steps/55-runtime-login.sh` + README row 8b + RO W10b (see deviations) | `test_ops_login__the_runtime_moves_to_its_dedicated_logins_by_name_only`: FAILED on the base (no step); SSM by name only (order asserted), no value on any argv or output, 0600 secrets file gone afterwards, the SQL program (ALTER ROLE, SCRAM verifier, `:6543`, `select current_user`, no `except`), envcheck on the staged bytes with `--network none`, 0600 env file, backup, restart, both `/readyz`; rerun `unchanged` (no restart); envcheck red → exit 3 untouched; not ready → exit 4 with the previous file back; missing parameter → refused, nothing run; no image → exit 2 |

## Truths established from the code (for the regime row and the launcher)

- **Ledger half after W10b.** certify's `tenant_ledger()` calls `cli.build_operations()`, which refuses a dedicated login (`infrx/operations/cli.py:44-57`) and reads `OPERATIONS_DATABASE_URL` first. Once step 55 has put `infrx_runtime` into the env file's `DATABASE_URL`, the run3 launcher's ledger half would be refused, and P-17 check 6 could not be shown. The new launcher therefore also passes `OPERATIONS_DATABASE_URL` = SSM `pg_journal_url` (the owner login) on :6543.
- **Where step 55 must sit.** Gateway and worker read only `/etc/marlin2b-gateway.env` (`deploy/marlin2b-gateway.service:13,34`, `infrx-worker.service:11,31`), and install.sh/preflight rewrites it from SSM at every 50-install (`DATABASE_URL` ← `pg_journal_url`, `preflight.py:158-159`). A step that runs between W7 and W10 would be overwritten, so step 55 runs after W10 (W10b) and after every later 50-install.
- **SSM convention.** preflight reads `--param-prefix /model-inference` + a lower-snake leaf (`pg_journal_url`, `monitor_database_url`, `marlin2b_api_key`; `preflight.py:153-167,346`); the steps use the same (`/model-inference/canary_key`, `operator_key`). The new names follow it: `/model-inference/infrx_runtime_password`, `/model-inference/infrx_monitor_password`. Supavisor's `<role>.<project-ref>` user form is the one `pilot.dedicated_login` recognises (`infrx/gateway/pilot.py:121-126`).
- **Step 55's SQL.** It ran for real against a task-local PostgreSQL 16. A CREATEROLE owner that created both roles NOLOGIN (as 0021 does) ran the step's Python twice. Both runs exited 0 and printed only `DATABASE_URL MONITOR_DATABASE_URL`, which shows the step is idempotent. `pg_authid` then showed both roles with `rolcanlogin` t and a `SCRAM-SHA-256$` verifier. Passwords with `/ @ : %` round-trip through the URL quoting. Both produced DSNs pass preflight's `pg_dsn` shape and contain no `FORBIDDEN_CHARS`, and `dedicated_login` returns True for both. The port was 55449 locally, via a sed of the one literal `:6543/`.
- **Known-good with the regime names.** A local run of `known-good.py <full sha> --applied 0023 --set` with the six names plus `ACCOUNTING_REGIME` and `ACTIVE_RATE_CARD_VERSION`, without `--bundles`, gave exit 0 and KNOWN-GOOD for both 4226315 and bda1586.

## Commands (worktree root unless noted; `api` = `apps/infrx-api`)

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env synced |
| baseline, api: `uv run --frozen --no-sync pytest -q ../../tests/integration/backend/recovery/test_runbooks.py tests/i/test_ops_steps.py tests/i/test_rollout.py` (base) | 0 | 28 passed |
| fails-before, api: same file set with the new cases, before the fixes | 1 | rb09, rb10, rb11 FAILED (reasons: stale seed; INFRX_SET without the regime; publish-card in §0); the step-55 and launcher cases FAILED (files absent) |
| `bash -n infra/rollout/e4c-certify.sh`; `bash -n infra/rollout/steps/55-runtime-login.sh` | 0, 0 | parse |
| `shellcheck` | - | not installed on this host (not run); `bash -n` and the stub runs stand in |
| `ruff check tests/integration/backend/recovery/test_runbooks.py apps/infrx-api/tests/i/{test_ops_steps,test_rollout,mutants}.py` (root and api) | 0 | All checks passed |
| api: `uv run --frozen --no-sync pytest -q ../../tests/integration/backend/recovery/test_runbooks.py tests/i/test_ops_steps.py tests/i/test_rollout.py` | 0 | 33 passed |
| api: `INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/i/test_mutants.py -k "login_ or launcher_ or well_formed or every_case"` | 0 | 10 passed: 8 new mutants killed, list well formed, every case covered (a first attempt was `broken_runner`: another lane held `/tmp/infrx-i8-pooler-55450.lock`; rerun once it was free) |
| api: `uv run --frozen --no-sync pytest -q tests/i` | 0 | 238 passed, 1 xfailed |
| api: `uv run --frozen --no-sync pytest -q ../../tests/integration/backend/recovery` | 1 | 40 passed, 33 skipped, **1 failed: `test_observe.py::test_i3b_ob10`** ("families with no panel: infrx_post_marker_refusals_total"). It is outside this lane's paths: no alert, dashboard or observe file was touched, so it fails on the base too |
| `apps/infrx-api/.venv/bin/python -m pytest -q models/marlin2b/tests/test_profile.py` (it parses E4C-runbook's commands) | 0 | 25 passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS; 942 local Markdown links checked across 250 documents |
| `git diff --stat 6cbb6a45..HEAD` | 0 | owned paths only (table above + this file + the update JSON) |

## Deviations

- **W10b, not W7b.** RO already uses W7b–W7e (operator seed, USD price, signup grant, alias rows). Step 55 also has to run after W10 because 50-install rewrites the env file from SSM. It still runs after W7, as the brief asked. The README row is 8b.
- **ON_ERROR_STOP.** The box has no psql, so step 55 runs its SQL through psycopg inside the installed image. The equivalent of ON_ERROR_STOP is that the program has no `except`: any SQL or login error exits non-zero, and `set -e` stops the step before the env file is touched. The test asserts the missing `except`, and the exit paths are exercised.
- **The launcher was copied, not edited.** It is now `infra/rollout/e4c-certify.sh`, and the session-03 file stays as run3's record. It also carries `OPERATIONS_DATABASE_URL`, which the brief did not name. This follows from item 5: without it the ledger half fails on the runtime login. It adds one SSM read by name on the box, with the same instance role preflight uses.

## Open issues

- **The SSM parameters do not exist yet.** `/model-inference/infrx_runtime_password` and `/model-inference/infrx_monitor_password` need the coordinator to create them before W10b, as in README step 2: a 0600 file and `--value file://`. Generating the passwords is the coordinator's job.
- **Unmeasured on hosted.** It is not known whether Supavisor on :6543 accepts a login moments after its `ALTER ROLE` (auth_query caching). The step's login check fails closed with exit 1 and leaves the env file untouched; a rerun is safe. ⚠️ TO BE VERIFIED at the window.
- **The monitor DSN does not survive a reinstall.** Only step 55 writes `MONITOR_DATABASE_URL` into the env file, and a reinstall drops it until step 55 reruns. A durable alternative is the SSM parameter `/model-inference/monitor_database_url`, which preflight already reads. That parameter would also serve O4's `MONITOR_DSN_PARAM`.
- **The runtime login has the same limit.** preflight's `DATABASE_URL` leaf is `pg_journal_url`, which step 55 and the ledger half use as the owner login, so the runtime login cannot move into SSM without a new preflight leaf, and preflight is not an owned path.
- **Unproven rollback.** `rollback.md`'s drill `--set` list (not owned) lacks `ENGINE_MAX_NUM_SEQS` and the two regime names. After W7f, a rollback to a legacy-only target is unproven (noted in RO §3).
- **Pre-existing failure.** The recovery `test_i3b_ob10` failure is outside this lane.

## Wiring requests

1. **15-pending-inputs.md** (a P-25 or new row): "`/model-inference/infrx_runtime_password` and `/model-inference/infrx_monitor_password` (SecureString; coordinator-generated, created from a 0600 file) are needed before rollout.md W10b / `55-runtime-login.sh` (RV-09, D10 wiring 6)."
2. **I8 / `infra/README.md` or the I8 evidence**: one line saying D10 wiring 6's login provisioning is `infra/rollout/steps/55-runtime-login.sh` (W10b). The privilege probe O7 then runs with `--role infrx_runtime --allow-functions research/plan/evidence/d/D10-runtime-functions.txt`.
3. **`infra/runbooks/rollback.md:62`**, the drill's `known-good.py --list` line: add `--set ENGINE_MAX_NUM_SEQS --set ACCOUNTING_REGIME --set ACTIVE_RATE_CARD_VERSION` so it passes every install name, as rollout.md §3 now does. The measured result with the eight names is in the Known-good bullet above.
4. **tracker**: E4C-RUNBOOK-2 row → review. The brief's §5 item (2) is done.

## Remaining effort

Remaining for this lane: optimistic 0.25 h, likely 0.5 h, pessimistic 1.5 h; confidence medium. The basis: everything in the brief's item (2) is committed and green, so the remaining time is review, and the fix loop if review finds something. The window itself is not in this estimate.

## Verification log

- 2026-09-26: written at implementation head e9b21287; the commands above ran in this worktree.
