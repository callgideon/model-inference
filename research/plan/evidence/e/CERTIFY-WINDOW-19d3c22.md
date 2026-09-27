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
