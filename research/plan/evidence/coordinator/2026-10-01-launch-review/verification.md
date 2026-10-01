# Launch review — verification record

Date: 2026-10-01. Reviewed source: `252f3ea8fda0d144fe03151840bb232fb1c76633`. Review host: macOS, Python 3.12.13 from the repository's frozen uv environment, pnpm 9.15.9. These are review checks, not a production release certificate.

## Setup and state

- Fast-forwarded clean main in `/Users/rey/Documents/GitHub/model-inference-v1-review` from `dff31efc` to `252f3ea8`. Rechecked origin/main after the review checks; unchanged.
- Left the separate dirty `/Users/rey/Documents/GitHub/model-inference` checkout untouched.
- `make api-env`, App and Lab `pnpm install --frozen-lockfile`: PASS. No lockfile changes.
- `docker info --format '{{.ServerVersion}}'`: FAIL — cannot connect to the local Docker daemon. Docker-dependent checks are unavailable here, not passed.
- `git diff --name-only 41693d5d..HEAD -- apps/app ':(exclude)apps/app/supabase' ':(exclude)apps/app/README.md'`: empty. Different deployed App/runtime commit labels are not by themselves evidence of a changed App API contract.

## Fresh checks

| Check | Result | Limits |
|---|---|---|
| `python3 research/plan/scripts/validate_plan.py` | PASS; 133 tasks, backend closure 50, App closure 64; final rerun checks 969 local links across 487 documents | Validates plan structure, not implementation acceptance. |
| `python3 research/plan/scripts/progress.py check` | 0 errors, 14 warnings; 133 tasks / 257 lanes; 16 unapplied update files | Stale estimates and W6 writer records; BACKEND-READY, APP-LOCAL and APP-PILOT still pending in the overlay. |
| Focused gateway/media pytest selection below | **432 passed, 54 skipped**, 2 deprecation warnings | Skips remain unexercised. This is not the complete API/real-service gate. |
| `make console-test console-lint console-typecheck` | **591 passed, 59 skipped**; lint 0 errors / 2 existing warnings; typecheck PASS | Initial test run precedes the fresh build. Real-service/browser cases need their dedicated targets. |
| `make console-built` | Build PASS; **22 tests passed, 0 skipped** | Includes browser-bundle privileged-variable and private-page prerender checks; does not execute a hosted browser journey. |
| `make lab-test lab-lint lab-typecheck` | **262 passed, 14 skipped**; lint/typecheck PASS | Real Lab stack cases skipped; does not prove enabled production workers. |
| `make api-lint` | PASS | Existing per-file exemptions remain; not a claim of no lint debt. |
| `make api-typecheck` | Exit 0, **458 errors against allowed baseline 458** | Baseline gate, not error-free type checking. |
| Combined `pytest -q models/marlin2b/tests tests/integration/backend/test_certify.py` | **168 passed, 2 failed** | One Linux-tool prerequisite failure; one reproduced test-order leak. Details below. |
| `pytest -q tests/integration/backend/test_certify.py` alone | **49 passed** | Pure certification-rule tests; no live inference/certification. |

Commands without an interpreter prefix in the table use `apps/infrx-api/.venv/bin/python -m pytest` from the repository root. Make targets use the committed Makefile. Output excerpts are in [check-output.txt](check-output.txt); excerpts are explicitly tails, not full logs.

Focused API selection, run from `apps/infrx-api`:

```sh
uv run --frozen pytest -q \
  tests/g/test_auth.py tests/g/test_catalog_truth.py tests/g/test_catalog.py \
  tests/g/test_startup.py tests/g/test_relay_readiness.py \
  tests/g/test_relay_sync.py tests/g/test_relay_sse.py \
  tests/g/test_relay_matrix.py tests/g/test_relay_recovery.py \
  tests/g/jobs/test_jobs.py tests/g/jobs/test_revocation.py \
  tests/g/jobs/test_result_expiry.py tests/g/uploads/test_uploads.py \
  tests/m/test_uploads.py tests/m/test_upload_restart.py \
  tests/m/test_cache_bounds.py tests/m/test_upload_wiring.py tests/m/test_retention.py
```

### Failure classification and minimal reproduction

1. `models/marlin2b/tests/test_e1b_l8.py::test_the_reference_half_is_a_declared_stop_that_silences_and_restores_the_alert_cycle`: the simulated Linux operational script invokes `flock`, unavailable on this Mac. Output says `flock: command not found`. It did not contact or stop a real engine. Rerun on the supported Linux host; do not treat it as a serving failure or silently waive it.
2. `tests/integration/backend/test_certify.py::test_e4b_a_box_rung_is_sized_to_hold_enough_short_clips_for_its_ttft_p95`: fails only after an earlier benchmark test replaces the shared `bench.load_corpus`. The individual case and the whole certification file pass independently. The two-test reproduction below gives **1 pass, 1 fail**, demonstrating the cross-file leak. The actual corpus has not been shown defective, and no evidence indicates pollution of the separate running E4C process.

```sh
apps/infrx-api/.venv/bin/python -m pytest -q \
  models/marlin2b/tests/test_bench.py::test_schedule_is_deterministic_and_independent_of_latency \
  tests/integration/backend/test_certify.py::test_e4b_a_box_rung_is_sized_to_hold_enough_short_clips_for_its_ttft_p95
```

Cause: `models/marlin2b/tests/test_bench.py:146–147` directly assigns the corpus-loader function and never restores it after the first selected test. The second case sees fake clips, `shorts(120) == 60`, and fails its real-corpus undersampling assertion. Fix test scope/restoration rather than altering production benchmark thresholds to silence the failure. [Full minimal-reproduction output](test-order-repro.txt).

Not run: full `make check`, all mutation lists, Docker-backed real-service/browser stacks, GPU tests, authenticated production requests, fault/load/restore/canary tests. The active E4C run belongs to the implementation/operator session and was not disturbed.

## Fresh production probes

Only unauthenticated HTTP GETs. Raw public JSON and redacted HTML observations are in [public-probes.json](public-probes.json).

| Time UTC | URL | Observation |
|---|---|---|
| 18:43:38 | `https://marlin2b.callbill.ai/health` | 200, `ok: true`; engine health only, not full readiness proof. |
| 18:43:39 | `https://marlin2b.callbill.ai/v1/models` | 200; one published model, available, approved CREDIT card, 82 s/64 MiB finite-video cap, sync/stream/async, nonzero serving retention. |
| 18:43:39 | `https://app.callbill.ai/api/version` | 200; commit `252f3ea8fda0d144fe03151840bb232fb1c76633`, production, built 18:24:01.949Z. |
| 18:43:39 | `https://app.callbill.ai/signup` | 200 HTML. Does not establish that hosted signup is enabled. |
| 18:46:01 | `https://lab.callbill.ai/` | 200; sign-in form present. |
| 18:43:40 | `https://lab-control.callbill.ai/lab/v1/releases` | 401 unauthenticated; expected boundary, not a successful authenticated listing test. |

A probe of Lab `/login` returned 404. Source confirms sign-in is rendered by the provider layout at `/`, so this is not classified as a Lab outage. An earlier `/api/version` probe also returned 404; this review has no fresh public proof of the deployed Lab web commit. Consumer runtime `41693d5d`, Lab control `7ecbab0e`, schema 0059 and worker flag states are attributed to the latest handoff, not independently inspected on the box.

## Latest-main merge verification — 2026-10-01, 20:40 UTC evidence cutoff

Pulled upstream main `6462ed06bbeae1220b92bc293f80503cebceb5ac` and merged the review/cleanup branch (`c90a3f61`) without conflicts. The new upstream files `certify.py`, `test_certify.py` and the session-03 operator record are byte-for-byte unchanged by the merge. Documentation/tracker reconciliation preserves the 18:58 E4C diagnosis; it does not turn the source oracle correction into a production pass or discard the independent envelope failure.

| Check on merged working tree | Result |
|---|---|
| `.venv/bin/python -m pytest -q tests/integration/backend/test_certify.py` (repository root, interpreter under apps/infrx-api) | 49 passed; separate process from the benchmark suite with its known test-state leak |
| Same interpreter, `research/plan/scripts/test_validate_plan.py research/plan/scripts/test_progress.py tests/integration/backend/recovery/test_runbooks.py` | 65 passed |
| API directory: `uv run --frozen pytest -q tests/i/test_known_good_proof.py` | 13 passed |
| Plan/ledger/link validator | 133 tasks, valid dependencies/gates, current ledger; PASS |
| Progress validator | 257 lanes, zero errors/warnings; gates unchanged |
| Invariant comparison against upstream | Task records/dependencies, release definitions, candidate gate evidence/decisions unchanged; no unresolved conflicts |
| Working/staged whitespace check | PASS |

At 20:40 UTC, public App `/api/version` returned 200 with source `6462ed06`, built `2026-10-01T20:06:14.249Z`, environment production. API `/health` and `/v1/models` returned 200. No authenticated request, signup, deployment, reset or production mutation was performed. The broader review tests above remain dated to `252f3ea8`; only the checks in this section were repeated for the merge. Origin was fetched again before committing and remained at `6462ed06`.
