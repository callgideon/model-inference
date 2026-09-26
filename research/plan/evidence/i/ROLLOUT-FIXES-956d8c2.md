# ROLLOUT-FIXES (track I8) — tooling defects from the rollout gates rehearsal at e607b705

- Branch `codex/rollout-fixes`, base `aedd086d`, code head `956d8c29` (commits `268f1d1e`, `40bff9f4`, `956d8c29`; this evidence is committed on top).
- Tooling and runbooks only; no product code, no migration, no product invariant touched. Nothing ran against the box, AWS, SSM or hosted Supabase: every `ssm.sh` run used a recording `aws` stub on PATH, and the rehearsals ran with an extra `aws` on PATH that exits 97 (the script's own SSM stub comes first).

## Per-item diff summary

| # | Change | Files |
|---|---|---|
| 1 | G5 harness: the worker `infrx_build_info` check fetches the whole `/metrics` body with `docker exec … python -c '…read().decode()'` and compares it as a variable (`check … '[[ $out == *"$want"* ]]'`); no response body is eval'd. `http()`'s 300-char truncation is left for the printed diagnostics. | `apps/infrx-api/deploy/rehearse.sh` |
| 2 | `ssm.sh`: `-h`/`--help` print usage, exit 0; the step must be an existing regular file (`[ -f "$step" ] \|\| { usage >&2; exit 2; }`), both before any aws call; `cat -- "$step"`; NAME=VALUE validation unchanged. | `infra/rollout/ssm.sh` |
| 3 | rollout.md §1 `INSTALL_ARGS` carries `DATABASE_POOL_MAX_SIZE=6` inside `INFRX_SET` (was a comment). 50-install: **ENGINE_MAX_NUM_SEQS is now required** (`: "${ENGINE_MAX_NUM_SEQS:?…}"`, like RELEASE and MIGRATION_DIGEST - the file's convention for operator statements) instead of defaulting to 32; chosen over a default of 8 so no silent value exists either way (every documented caller - rollout.md INSTALL_ARGS, rollback.md step 4 - already passes it). README step 8 points at `INSTALL_ARGS` and names `ENGINE_MAX_NUM_SEQS=8`. | `infra/runbooks/rollout.md`, `infra/rollout/steps/50-install.sh`, `infra/rollout/README.md` |
| 4 | 45-s3-check heredoc: pytest 8.4.2 → 9.1.1 with uv.lock's wheel hash `sha256:37a86b45…4f0c` (GHSA-6w46-j5rx-g56g / CVE-2025-71176). pluggy 1.6.0, iniconfig 2.3.0, packaging 26.3, pygments 2.21.0 rechecked: already uv.lock's version and wheel hash (the new test proves it); the header's "uv.lock's wheels" is now true. | `infra/rollout/steps/45-s3-check.sh` |
| 5 | Guard, not the release archive: one identical loop at the top of 71/72/74/80/86 (after `repo=`, before any command) and at the top of 81's `fetch()` - `BLOCKED: this step needs an I8+ checkout (missing <path>)`, exit 3. Why the guard: three lines per step, fail-closed, no change to what each step reads; running from `git archive` (30-pause's pattern) would change 72's pinned-copy source, 80/81's serving-version pins and 86's known-good record source - a larger diff with new behaviour to prove. Paths: 71 `infra/runbooks/pool_budget.py`; 72 `infra/observe/systemd` + `infra/alerts/operations.json`; 74 `infra/observe/deliver.py`; 80/81 `infra/runbooks/artifacts.py`; 86 `infra/rollout/known-good.json` (verified absent at bda1586 and 4226315 with `git ls-tree`). 81's guard sits in `fetch()` so `MODE=undo` (reads nothing from the checkout) stays available as the recovery path; `canary.sh` was dropped from 81's list because `test_artifacts.py` stages a checkout without it for fetch mode and pre-I8 trees lack `artifacts.py` anyway. | the six steps |
| 6 | README: RELEASE on `claude/consumer-v1`; G3 "false by design" removed (history in the log); G6 names the existing `pg_journal_url` (2026-09-24T01:03Z); rollback row: after 0019+ no recorded target qualifies until a `schema_proof` exists (KNOWN-GOOD-PROOF lane), R3 maintenance the fallback; G1 notes the symlinked `.venv` untracked entry (use `make api-env`); dated verification-log entry appended. rollout.md log entry appended. | `infra/rollout/README.md`, `infra/runbooks/rollout.md` |

Tests/mutants: `tests/i/test_rollout.py` (+3 cases, 1 updated), `tests/i/test_ops_steps.py` (+1 case; the existing 71 case now points the step at the repository checkout), `tests/i/mutants.py` (+10 mutants, 2 anchors updated: `cutover_drops_engine_concurrency`, `ssm_drops_arguments`).

## Fails-before (each run before its fix)

| Case | Before |
|---|---|
| `test_backend_deploy__ssm_refuses_what_is_not_a_step_before_any_aws_call` | FAILED: `--help` exit 0 with cat's help base64-sent to the (stubbed) aws: `status Success`, stub log non-empty |
| `test_backend_deploy__the_cutover_keeps_the_engines_concurrency` (updated) | FAILED: `install INFRX_SET=[ENGINE_MAX_NUM_SEQS=32 ] pilot restart`, exit 0 without the argument |
| `test_backend_deploy__the_bucket_check_installs_exactly_uv_locks_pytest_wheels` | FAILED: `('pytest', '8.4.2', '9.1.1')` |
| `test_backend_deploy__the_runbooks_install_args_fit_the_session_pooler` | FAILED (rollout.md reverted in place, then re-applied): `FAIL session: peak 21 + headroom 2 > limit 15`, exit 1 |
| `test_ops_continuous__a_step_on_a_pre_i8_checkout_is_blocked_before_it_acts` | FAILED: 71 exit 2 `no INFRX_IMAGE in /etc/marlin2b-gateway.env` (ran on past the missing checkout) |
| rehearsal item 1 | 47 PASS / 1 FAIL (below) |

## Commands (worktree root unless noted; api = `apps/infrx-api`)

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env built in the tree (not a symlink) |
| api: `uv run --frozen --no-sync pytest -q tests/i/test_rollout.py tests/i/test_ops_steps.py` | 0 | 20 passed (base: 16) |
| api: `uv run --frozen --no-sync pytest -q tests/i/test_artifacts.py tests/i/test_ops_steps.py tests/i/test_rollout.py tests/i/test_rollback_drill.py` | 0 | 30 passed |
| api: `uv run --frozen --no-sync pytest -q tests/i --deselect …test_mutant_is_killed --deselect …test_the_runner_reports_the_right_outcome` | 0 | 183 passed, 48 deselected, 1 xfailed |
| api: `uv run --frozen --no-sync pytest -q tests/i/test_mutants.py` (default list) | 0 | 50 passed (first run at 40bff9f4: 49 passed, 1 failed - `ssm_drops_arguments` misdeclared by the `cat --` edit; anchor fixed in 956d8c29, rerun green) |
| api: `uv run --frozen --no-sync python tests/i/mutants.py <10 new + cutover_drops_engine_concurrency>` | 0 | killed: `ssm_sends_a_non_step`, `cutover_engine_default_restored`, `s3_check_stale_pytest`, `install_args_pool_pin_dropped`, `pre_i8_guard_dropped_{71,72,74,80,81,86}`, `cutover_drops_engine_concurrency` (11/11) |
| api: `uv run --frozen --no-sync python tests/i/mutants.py <every mutant whose file this lane changed>` | 0 | 39/39 killed |
| `bash -n` on rehearse.sh, ssm.sh, steps 45/50/71/72/74/80/81/86 | 0 each | clean |
| `PATH=<noaws>:$PATH REHEARSAL_NS=infrx-rollout-fixes apps/infrx-api/deploy/rehearse.sh <scratch>/reh-before/work` at `268f1d1e` (rehearse.sh unchanged from base) | 1 | **47 PASS / 1 FAIL**: `FAIL the worker's /metrics carries infrx_build_info for this release and image`, preceded by `eval: line 50: syntax error near 'database'` / `# HELP infrx_db_pool_connections Connections of this process's database pool.`; `REHEARSAL FAILED`; `teardown: nothing infrx-rollout-fixes-* left` |
| same, `<scratch>/reh-after/work` at `40bff9f4` | 0 | **48 PASS / 0 FAIL**, `REHEARSAL PASSED`, `teardown: nothing infrx-rollout-fixes-* left`; afterwards `docker ps -a --filter label=ai.infrx.rehearsal=infrx-rollout-fixes`, networks and volumes named `infrx-rollout-fixes*`: 0 each |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (re-run with this file) |

A first "before" attempt (43 PASS / 5 FAIL) is void: the tree was edited during the run, so install.sh's second deploy refused `the checkout has uncommitted changes`; it was rerun on the committed tree with nothing edited until teardown.

## Wiring requests

None.

## Open issues

- Other rehearse.sh checks still interpolate short response bodies into `eval` strings (e.g. step 5/6 `/health`, maintenance bodies); they pass today because those bodies carry no apostrophe. Same fix pattern if one ever does; not changed here (scope).
- KNOWN-GOOD-PROOF (a `schema_proof` for bda1586/4226315 once 0019+ are applied) remains its own lane; the README now says so.

## Remaining effort

Optimistic 0 h / likely 0.25 h / pessimistic 1 h (review follow-ups), confidence high; basis: all six items implemented with passing suites, mutants and a green rehearsal.
