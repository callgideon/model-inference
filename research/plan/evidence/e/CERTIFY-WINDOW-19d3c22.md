# CERTIFY-WINDOW (19d3c22): the E4C certify window's box steps, sequencer and H6

- Lane `codex/certify-window`, base `b5339c55`, head `19d3c229` (code); this record is the next commit.
- Input: the read-only preparation `certify-prep.md` (397 lines) and its `steps/`, `h6.sh`, `fill.py`, `validate.py`, `local-e4c/`.
- Nothing ran against the box, AWS, SSM, hosted Supabase, Vercel or a secret. Every script ran locally, against recording stand-ins on PATH (docker, curl, aws, systemctl, vmstat, ssm.sh, operator-cli.sh, verify-journey.sh), real git repos and bundles, or `DRY_RUN=1`.

## Changed paths (`git diff --stat b5339c55..19d3c229`: owned paths only)

| Path | What |
|---|---|
| `infra/rollout/steps/76-e4c-prepare.sh` (new) | Runs the A1 edge 200 check. Moves `/opt/dlami/nvme/w3-checkout` to RELEASE from the verified W1 bundle, running `sha256sum -c` before the fetch; the deploy checkout is never named. Builds `infrx-certify:$RELEASE` once, per E4B-9aa7ffe.md:430. Retakes `e4b/inventory.txt` and keeps the old file as `.prev`. Exit 2 changes nothing. Exit 3 means no `image_equals_pin=yes`, and the old inventory is kept |
| `infra/rollout/steps/77-e4c-profiles.sh` (new) | Reads `deployed_sha` from both `/metrics`, the image from docker and the W10 units from systemd. `config_version` is the env file's hash. Fills the six bases; the heredoc is `certify-fill.py` verbatim. Writes `keys-certify.json` and installs all seven files 0644. Validates each file in the certify image with `--network none` (exit 3 unless runnable with no errors) and prints `certify --hashes` |
| `infra/rollout/steps/79-wc0-scrape.sh` (new) | WC-0 start and stop: one detached sidecar at a time. Exit 3 when no scrape line appears |
| `infra/rollout/steps/78-e4b-report.sh` | Defect 3: the default run is the newest UTC-named directory, never `e4c/` or `e1b-*`. No run at all exits 3 |
| `infra/rollout/steps/80-e4b-fetch.sh` (new) | `e4b-fetch3.sh` parameterized by `RUN`, which must be a UTC name. Refuses a run with no `report.json`. Prints the archive sha256 |
| `infra/rollout/certify-window.sh` (new) | The resumable sequencer. Its 25 steps run in the brief's order. Flags: `--step`, `--only`, `--logdir`. `DRY_RUN=1` prints the plan and its stop conditions. Long cells run detached (`setsid -f`) and are polled; a resume re-attaches to a live cell, never relaunches it, and retries a failed one. The certify run is never relaunched from its LOGDIR. The report step polls 78 with `RUN=`. H5 uses `statement --key-file` with a 0600 file that is shredded before `adjust`. `freeze.json` and `profiles.sha256` are built from the 76/77 logs, the SSM ids and the launchers' sha256. Each drill asks first |
| `infra/rollout/certify-h6.sh` (new) | H6 (from the prep's `h6.sh`): `key_id[:8]` of active non-operator keys. The operator key is printed apart. Exit 1 (`STOP:`) on a failed dry run or an extra spending key |
| `infra/rollout/certify-fill.py`, `certify-validate.py` (new) | The prep's `fill.py` and `validate.py`. The validator now exits 1 unless every cell is runnable, and writes its stamped copies to a temp dir |
| `infra/rollout/README.md` | Adds §5 (a row per new script) and a log line |
| `models/marlin2b/results/E4C-runbook.md` | Wording only. Defect 1: H6 and §5.0 say "key id prefix" (`key_id[:8]`) and H6 excludes the operator key. Defect 2: §3 names 76/77 and the three preconditions. Defect 7: the closing H6 after §4 and §4a. Defect 8: H5 says "about 50,000", and the worker drill uses `systemctl kill`. Plus a log line |
| `apps/infrx-api/tests/i/test_ops_steps.py`, `test_rollout.py`, `mutants.py` | 11 new cases and the step-list update. 48 new `certify_*` mutants. `_layout` now copies `runprofile.py` and `corpus/manifest.json` |

## Commands

| Command | Exit | Result |
|---|---|---|
| `bash -n` on certify-window.sh, certify-h6.sh, steps 76/77/78/79-wc0/80-e4b-fetch | 0 | clean |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/i/test_rollout.py tests/i/test_ops_steps.py` | 0 | 39 passed, 1 skipped. The skip is the existing PostgreSQL-only case at `test_ops_steps.py:822`, which needs `INFRX_D_TASK` |
| `uv run --frozen pytest -q tests/i/test_mutants.py -k "well_formed or every_case"` | 0 | 2 passed |
| `uv run --frozen python tests/i/mutants.py <certify_* login_* launcher_* e1b_window_*>` | 0 | **75/75 killed**: 48 new and 27 existing |
| `uv run --frozen pytest -q ../../tests/integration/backend/recovery/test_runbooks.py` | 0 | 13 passed. No pinned phrase moved |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS ×3 (934 links, 288 documents) |
| `DRY_RUN=1 LOGDIR=<scratch> bash infra/rollout/certify-window.sh` | 0 | 44 `plan` lines and 19 `expect` stop conditions, in order. The fakes on PATH were never called; `test_rollout.py` pins this |
| `certify-validate.py <repo> <filled> <e1b-window DRY_RUN plan>` | 0 | `ALL RUNNABLE`, 18 cells. The six fills are byte-identical to the prep's `local-e4c` copies (sha256 pinned in `PREPARED`) |

## Fails-before, one per decision

The mutants are the failing-before proof: each removes exactly one decision, and its case fails.

- First run: 48/49 killed. `certify_76_failed_inventory_installed` survived, because a failed `inventory.sh` never prints `image_equals_pin=yes`. The `rc` half of the guard was redundant, so it was removed together with its mutant. The retained guard is `image_equals_pin=yes`.
- Final run: 75/75 killed.
- A single-mutant run is `broken_runner` while another lane holds `/tmp/infrx-i8-postgres-55450.lock`, because the list's pristine baseline includes the pooler cases. The final run waited for the lock.

## Deviations

- 76's inventory guard is `image_equals_pin=yes`, not "exit 0 and pinned", because `inventory.sh`'s trailing registry probe can exit non-zero on a good inventory.
- H6 also stops on a non-zero dry run.
- `--only <step>` was added beside `--step`/`--logdir`.
- The sequencer's host copies live under LOGDIR (`$LOGDIR/e4c/`), not `~/e4c`.
- `journey-legs` runs the runbook's own §5.1 sync and foreign-call blocks, extracted at run time.
- Only two drills have a box form: the engine restart probes `127.0.0.1:8001/readyz` within 300 s, and the worker SIGKILL probes `:8002/readyz` within 30 s. The others are recorded NOT RUN unless run by hand.

## Open

- The window has not run. The SSE journey and replay are BLOCKED on `MEDIA_BASE_URL`. The canary re-enable is BLOCKED (P-24). O4–O6 are skipped by the user's decision.
- BACKEND-READY stays PENDING on P-17 checks 1, 5 and 7.
- `journey-legs` needs `VIDEO_FILE`, an in-cap clip on the host. `wc9` needs `CORPUS_CACHE`. `h4-check` needs `TENANT2_USER`.
- Revoke the tenant-2 key before any certify rerun, then retake H6.
- Defect 4 (`72-observe-install.sh` writes an empty value for an SSM name it cannot read) is not in this lane's paths and is unchanged.

## Estimate (the window itself, coordinator)

Optimistic 8 h, likely 10 h, pessimistic 14 h. Confidence: medium. Basis: certify-prep §4. The 4 h soak, 1.0–1.6 h of E1B cells and 1–3 h of drills dominate; the pessimistic figure adds one certify rerun (05 §7).

## Fix round (2026-09-27; handback f60636b1, code head 9e62f07f)

Fixes 1-CW-R1 (blocking), 0-CW-1/1-CW-R2, 0-CW-2 and 0-CW-3. Commits `8712b754` (fixes, tests, mutants) and `9e62f07f` (test timing). Owned paths only: `certify-window.sh`, `steps/78-e4b-report.sh`, `README.md` §5, `tests/i/test_rollout.py`, `tests/i/mutants.py`. Nothing ran against the box, AWS, SSM, hosted or a secret.

| Finding | Fix |
|---|---|
| 1-CW-R1, 0-CW-3 (a cell or the soak overlapped by a resumed step) | `certify-window.sh` takes `flock -n` on `$LOGDIR/.lock` at startup and exits 2 when another sequencer holds it. Detached cells and poll sleeps close fd 9 (`9>&-`), so a killed sequencer's live cell never holds the lock against the resume. Before each step (DRY_RUN excepted), `guard` STOPs when another `$LOGDIR/<cell>.pid` is live and has no `.rc`, naming the cell. A step after `report` also STOPs until `window.log` holds `certify finished: exit N (run <the run in certify.log's out=>)`. `report` records that line before an exit-1 STOP, because the run has ended either way |
| 0-CW-1, 1-CW-R2 (`exit N` cut off by SSM's 24,000 characters) | 78 prints `certify exit N` (the last `^exit N$` of certify.log) right after `== run`. The poll reads only that line. The real run1 through 78 is 25,103 characters with `exit 1` at 25,096 (none in the first 24,000). The new 78 gives 25,118 characters, and `certify exit 1` is in the first 24,000 |
| 0-CW-2 (engine drill PASS on the gateway's /readyz) | The engine drill probes the worker's `127.0.0.1:8002/readyz`, which is 200 only when the engine answers ready; this is E4C §6's "`/readyz` 200 ≤ 300 s". A background sampler in each generated drill script records whether the probe went non-200. PASS needs that outage seen, a 200 again, and `measured_s ≤ bound`. A `probe …: outage seen …` line precedes the unchanged `drill=` record |

### Commands

| Command | Exit | Result |
|---|---|---|
| New cases against the handback code (`8712b754`'s tests on `f60636b1`'s scripts) | 1 | 4 failed. **report_polls** expected `certify finished: exit 3 (run …)` and got the old line. **report_sees_certify_exit_past_ssm_24000_characters** got a STOP "no 'exit N'", exit 1. **no_step_starts_on_a_live_cell…** saw WC-6a dispatched with WC-7 live, exit 0. **a_drill_passes_only_after…** got `verdict=PASS measured_s=0` from a probe that never went down. **a_killed_sequencer_leaves_its_live_cell_resumable** passed: it guards the new lock and has no before-state |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/i/test_rollout.py tests/i/test_ops_steps.py` | 0 | 43 passed, 1 skipped (the existing `INFRX_D_TASK` case). The certify-window subset passed 8 times in a row after `9e62f07f` |
| `uv run --frozen pytest -q tests/i/test_mutants.py -k "well_formed or every_case"` | 0 | 2 passed |
| `uv run --frozen python tests/i/mutants.py <certify_* login_* launcher_* e1b_window_*>` | 0 | **85/85 killed**: the 10 new ones and the 75 from before. It ran after `/tmp/infrx-i8-postgres-55450.lock` was free, because the list's pristine baseline includes the pooler cases |
| `uv run --frozen pytest -q ../../tests/integration/backend/recovery/test_runbooks.py` | 0 | 13 passed. No pinned phrase moved |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS ×3 (934 links, 289 documents) |
| `DRY_RUN=1 LOGDIR=<scratch> PATH=<fake aws/docker/ssm>:$PATH bash infra/rollout/certify-window.sh` | 0 | 44 `plan` lines and 19 `expect` lines, as before. No fake was called. The drill plans read `/8002/readyz non-200, then 200 within 300s` and `30s` |

### New cases and mutants

| Case (test_rollout.py) | Mutants (tests/i/mutants.py) |
|---|---|
| `…report_sees_certify_exit_past_ssm_24000_characters`: the real 78 runs behind an ssm.sh stand-in that keeps 24,000 characters, with a 25,000-byte report.json. A run without `exit` STOPs | `certify_78_exit_after_the_json`, `certify_window_report_reads_the_tail` |
| `…report_polls_78…` (extended): the finish is recorded with its run, and on exit 1 as well | `certify_window_finish_unrecorded_on_fail` |
| `…no_step_starts_on_a_live_cell_a_running_certify_or_a_second_sequencer`: a live `wc7.pid` blocks `--only wc6a`. So do a launched run with no finish seen, and a finish recorded for an older run. A held `.lock` exits 2. None of these calls ssm.sh. With the finish seen and no live cell, WC-6a runs | `certify_window_live_cell_ignored`, `certify_window_cell_during_certify`, `certify_window_finish_of_any_run`, `certify_window_no_lock` |
| `…a_killed_sequencer_leaves_its_live_cell_resumable`: the sequencer is SIGTERMed mid-cell, and `--only wc7` re-attaches with ssm.sh called once | `certify_window_cell_holds_the_lock` |
| `…a_drill_passes_only_after_its_probe_saw_the_outage_and_the_engine_back`: the generated engine script runs locally against fake curl/systemctl/sleep. A probe that never goes down gives FAIL; one that goes down and comes back gives PASS. Every probe is `127.0.0.1:8002/readyz` | `certify_window_engine_drill_gateway_probe`, `certify_window_drill_outage_unseen` |

### Deviations and open items

- The guards cover one LOGDIR. A second LOGDIR on the same box is not seen, because this lane does not run a box-side `docker ps` check before each step. `e1b-window.sh` already refuses while a certify container runs. `e4c-certify.sh` (not in this lane's paths) still kills a live certify container without checking for `infrx-e1b-*`.
- A certify run that hangs and is killed by hand never prints `exit N`. The steps after `report` then stay refused in that LOGDIR, and a new qualifying run (05 §7) starts a fresh `certify.log`.
- A stale `.pid` whose pid was reused after an operator-host reboot blocks the next step. The STOP names the file.
- The lock is `flock -n`, so a resume within about 0.1 s of killing a sequencer can meet one of that sequencer's own short-lived children.
- E4C-runbook §6 already says "`/readyz` 200 ≤ 300 s" for the engine restart. The runbook is unchanged.
- The estimate is unchanged: 8 / 10 / 14 h for the window itself, medium confidence.
