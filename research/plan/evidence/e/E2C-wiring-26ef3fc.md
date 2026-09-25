# E2C wiring — coordinator requests applied on `codex/e2c-wiring`

Follows [E2C-3a113dc](E2C-3a113dc.md) and [E2C-f61d2f0](E2C-f61d2f0.md) (unchanged). Local only:
no hosted DB, box, AWS or SSM; nothing pulled; no container created or removed by this lane.

| Field | Value |
|---|---|
| Base | `5bef53bc` (claude/consumer-v1, contains the E2C merge) |
| Head evidenced | `26ef3fc3` (this report follows in its own commit) |
| Worktree / branch | `.claude/worktrees/codex-e2c-wiring` / `codex/e2c-wiring` |

## Commits

| # | Commit | Change | Test (fails before → passes after) |
|---|---|---|---|
| 1 | `fb50cbbb` | `Makefile`: `consumer-local`, `backend-certify`, `app-e2e` (`tests/integration/<gate>.sh $(GATE_ARGS)`) and `backend-local` (`$(API)/.venv/bin/python tests/integration/backend/e3c/runner.py --out "$${E3C_OUT:-$${TMPDIR:-/tmp}/infrx-e3c}" $(E3C_ARGS)`), all `.PHONY` | `tests/integration/test_preflight.py::test_each_gate_has_a_make_target_that_runs_its_wrapper` (4 params, `make -n`): 4 passed. Old Makefile: `make -n consumer-local` / `backend-local` exit 2. No Makefile test existed in `tests/i` or `tests/integration` |
| 2 | `98662fe6` | `apps/infrx-api/pyproject.toml` dev `pytest>=9.0.3,<10` (GHSA-6w46-j5rx-g56g / CVE-2025-71176); `uv lock`: "Updated pytest v8.4.2 -> v9.1.1", lock diff = pytest entry only (44 packages resolved) | `uv sync --frozen --all-extras` exit 0 (pytest 9.1.1); `uv run --frozen pytest -q tests/contracts -k 'not pg'` exit 0: **1260 passed, 6 deselected** (95 s); integration lane tests under 9.1.1: 138 passed |
| 3 | `4b3eee53` | `apps/infrx-api/deploy/rehearse.sh`: `MINIO_IMAGE=pgsty/minio@sha256:b6bfe723…d602372`; the former quay.io pin kept in a comment with its 401 reason | `tests/i/test_packaging.py::test_the_rehearsal_minio_is_the_integration_stacks_pullable_pin` (rehearse.sh pin == compose.yaml pin, pgsty digest); old file has the quay pin (fails). `tests/i/test_packaging.py` 23 passed; `bash -n` ok. Live `rehearse.sh` not run (needs pull/provision) |
| 4 | `26ef3fc3` | E2C-FR-2, `tests/integration/run.py`: `suites(..., leave_out=())` adds `--ignore=<path>`; `_run` passes `leave_out=(BACKEND_SUITE,)` at `--layer 3` only — the backend stage already ran that suite with PostgREST up and tore PostgREST down (kept down: its pool blocks the mutants' DROP DATABASE) | `tests/integration/test_run.py::test_layer_three_suites_leave_out_the_backend_suite_its_own_stage_ran` (harness stubbed; layers 3/all/1): **failed before, passed after**. `test_run.py test_preflight.py test_harness.py`: **138 passed** (incl. the mutant-anchor check) |

## 5. consumer-local gate measurement (fix agent's driver)

Driver (PID 379488) **finished**: exited, `docker ps -a --filter name=infrx-e2c-` empty (nothing
left; nothing removed by me). Log `<coordinator scratchpad>/e2c/gate-run.log`, verdict `<coordinator scratchpad>/e2c/gate-out/verdict.json`.

verdict.json header: `{"schema": "infrx.e2c.verdict/1", "gate": "consumer-local", "verdict": "FAIL",
"exit": 1, "head": {"sha": "330e04f1", "dirty": true}, "started": "2026-09-25T01:49:49+00:00",
"finished": "2026-09-25T02:52:18+00:00"}`, argv deviations `driver:e2c-ports-55493-55494`,
`driver:integration-l3-blocked-by-lane-rule`. Run in the `codex-e2c-verify` worktree.

| Stage | Verdict | Tests | Passed | Failed | Errors | xfail | Cause |
|---|---|---|---|---|---|---|---|
| preflight:consumer-local | PASS | | | | | | ports 55448/55493/55494 |
| s3 | PASS | | | | | | `infrx-e2c-s3` started on 55494 |
| api-contracts | PASS | 1078 | 1078 | 0 | 0 | 0 | |
| api-d | FAIL | 667 | 661 | 1 | 0 | 5 | `test_pgharness.py::test_the_decoy_is_the_tasks_own_and_d1s_is_unchanged` `('d2', 55473)`: the lane branch's tasklocal (decoy collision); **passes at `5bef53bc`/this head** (`-k decoy`: 1 passed) |
| api-d-mutants | NOT RUN | | | | | | E2C-FR-1 (signup Runner env) not applied |
| api-g / i / j / q / t | PASS | 582 / 159 / 263 / 192 / 63 | all | 0 | 0 | 0 | |
| api-m | FAIL | 449 | 424 | 12 | 13 | 0 | S3 cases: connection refused on 127.0.0.1:55494 |
| api-w | FAIL | 265 | 258 | 1 | 6 | 0 | same (`infrx-worker-main` bucket, 55494 refused) |
| bench-test | PASS | 68 | 68 | 0 | 0 | 0 | |
| integration-l1 | BLOCKED | | | | | | 103 layer-2 cases skipped inside the runner |
| integration-l3 | BLOCKED | | | | | | forced (lane-isolation: e2 namespace is the coordinator's) |
| backend-local | NOT RUN | | | | | | E3C runner absent in that tree |

The api-m/api-w failures are not product defects: `infrx-e2c-s3` was gone before the api stages
ended (`s3-down.log`: "No such container: infrx-e2c-s3"), so every S3 case after that got
connection refused. Who removed it is not recorded (another process's cleanup of the e2c prefix
is the likely cause; the killed first fix agent left one earlier). Not a PASS: rerun needed.

## Open items

- consumer-local not PASS: rerun on this head (tasklocal resolved, FR-2 in) with the S3 container
  protected; api-d-mutants stays NOT RUN until E2C-FR-1 (`tests/d/signup_mutants.py` Runner env).
- integration-l3 PASS still undemonstrated (coordinator runs layer 3, e2 namespace).
- `backend-local` target points at `tests/integration/backend/e3c/runner.py`, absent until E3C
  merges (`make backend-local` fails until then).
- pytest 9: only `tests/contracts` (not pg) and `tests/integration` lane tests run here;
  `make api-test` / `make api-mutants` on 9.1.1 (tests/d, tests/q untried) still owed at merge.
- Host sysctl `55500-55599` (E2C request 4) not applied (root).

## Estimate

Remaining for E2C: optimistic 1 h, likely 2 h, pessimistic 4 h; confidence medium. Basis: one
consumer-local rerun (~65 min wall) plus E2C-FR-1 and the api-test/api-mutants proof on pytest 9.

## Verification log

- 2026-09-25: wiring 1-4 applied with regressions; gate measurement read from the finished driver.
