# api-lifecycle (LW7 batch 1) — AP-11 slices 11a, 11b — evidence at ea353c1

Lane api-lifecycle, branch `codex/w7-api-lifecycle`, worktree
`.claude/worktrees/codex-w7-api-lifecycle`, base `cd9f517c` (wave-7 keystone), head of the
evaluated code `ea353c12`. Task-local key `ap11` (PostgreSQL 57567, Valkey 57568, MinIO 57569).
Nothing hosted, no box, no AWS/SSM/S3, no Vercel, no secrets; `probe.py` untouched and uncalled.

## Commits

| Commit | Slice | What |
|---|---|---|
| ba8525cc | 11a + 11b (layer 1) | runner, 0600 state + separate 0600 secrets, modes, resume/reconcile, redaction, exit codes; the 18 stage contracts; 31 cases on a contract fake |
| 60489971 | isolated mode | `world.py`: ap11's own stack with declared fixtures; BLOCKED[stack] when it cannot compose |
| ea353c12 | mutants + docs | 70-mutant list, 16 product-defect oracles, partial results on INVALID, runbook, evidence README section |

## Changed paths (all owned)

`tests/integration/api_lifecycle/` (new: `__init__.py`, `runner.py`, `state.py`, `world.py`,
`stages/__init__.py`, `stages/consumer.py`, `conftest.py`, `test_runner.py`, `mutants.py`,
`test_mutants.py`), `infra/runbooks/api-lifecycle.md` (new),
`research/plan/api-lifecycle/evidence/README.md` (the runner's section, appended),
`research/plan/evidence/w7/` (this file + the isolated verdict). The wrapper
`tests/integration/api-lifecycle.sh`, the Makefile target/list and the isolation case are
wiring requests (below), per the coordinator's note.

## What the runner does (11a / 11b)

- State file 0600, atomic (tmp 0600 + fsync + rename): run id, target, per mutation the
  Idempotency-Key, canonical request hash (`sha256:` of sorted JSON incl. body-bytes digest),
  `pending` BEFORE the request leaves and `done` + safe outputs after, stage checkpoints with
  redacted evidence, owned resources with their cleanup route, request counters. Secrets file
  0600, read only. A group/world-readable state or secrets file, a state of another target,
  or a recorded mutation re-sent with another body is INVALID (exit 4) before any request.
- Restart: a PASS stage is not repeated; a `done` mutation is confirmed by GET (vanished =
  FAIL), never re-sent; a `pending` one is retried with its ORIGINAL key and does not spend
  the budget again; a transport error/timeout/signal is NOT RUN "interrupted ... resume" and
  nothing runs after it.
- Modes: `inspect` (01 only; any non-GET refused before it leaves), `isolated` (+`--world
  ap11`), `live` (refused unless origins, identities, target, an exact `{amount, unit}` budget
  and `max_requests` 1..6, no fixtures), `cleanup` (state-owned resources only; 404/410 =
  gone). Consumer keys: API-minted (stage 08, AP-03) or a declared isolated fixture only.
- Exit 0 PASS / 1 FAIL / 3 BLOCKED, NOT RUN / 4 INVALID; gate = worst selected stage
  (FAIL > INVALID > BLOCKED > NOT RUN > PASS); a stage that asserted nothing is never PASS.
  `complete_lifecycle` only for live with all 18 PASS; api_boundary/quality/operations NOT RUN.
- Evidence per stage: UTC start/end, route TEMPLATE, origin, status, X-Request-Id,
  X-Inference-Id, Location, ms; assertions; versions (08: the listing); counters; owned ids;
  fixtures used. Redaction: every secrets-file value, bearer/sk-/JWT/URL-password shapes and
  credential-named fields, in the verdict, stdout and the state checkpoints.
- Stage contracts: titles and "what must be proved" are verification.md's rows verbatim (a
  case parses the table); each target route is a contracts.md path (a case checks) with its
  AP owner: 01 AP-01; 02/03 AP-04; 04 AP-05 (+GPU target); 05 AP-05/06; 06 AP-06 (+GPU);
  07 AP-06; 08 AP-01/03; 10/11 AP-02; 12 AP-07; 13 AP-07 composition; 14 AP-08 (+P-10);
  15 AP-02/08; 16 AP-08; 17 AP-07/08; 18 AP-05/06. `stages.accepted_operation` validates R270's
  202 + Location + `OperationDoc` for the stages AP-04/05/06 unblock.

## Commands, exit codes, counts

| Command (repo root unless noted) | Exit | Result |
|---|---|---|
| `apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/api_lifecycle/test_runner.py` (before any implementation) | 2 | RED: `ImportError: cannot import name 'runner' from 'api_lifecycle'` (collection error) |
| same, after ba8525cc | 0 | 31 passed |
| same, at ea353c12 | 0 | 47 passed (incl. 16 product-defect params) |
| `... pytest -q -p no:cacheprovider tests/integration/api_lifecycle/` | 0 | 53 passed |
| `INFRX_MUTANTS=all ... pytest -q -p no:cacheprovider tests/integration/api_lifecycle/test_mutants.py` (first full run) | 1 | 70 passed, 4 failed: `reconcile_skipped` survived (case read 01's probe GET), `sse_end_unchecked` survived (content check masked it), `no_target_accepted` and `inspect_selects_mutations` broken_runner (KeyError / StopIteration deaths) - all four fixed in ea353c12 |
| same, at ea353c12 | 0 | **74 passed**: 70/70 mutants killed, list well-formed, `test_every_case_is_covered_by_a_mutant` green (25 named cases), both self-tests (no false kill) |
| `runner.py --mode isolated --world ap11` (first try, at 60489971's parent) | crash | world: worker refused `INFRX_RELEASE_SHA` not 40-hex - fixed; composition errors now map to BLOCKED[stack] |
| `runner.py --mode isolated --world ap11 --state <0700 dir>/state.json --out <dir>` at ea353c12 | 3 | **BLOCKED** (expected): 09 PASS (7/7); 01 5/5, 08 1/1, 10 4/4, 11 5/5 assertions green and BLOCKED on AP-01/02/03; 02-07, 12-18 BLOCKED naming AP-04/05/06/07/08 (+GPU target, P-10, trace composition); 18 requests, 5 inference; 29.8 s; containers removed after; verdict `research/plan/evidence/w7/AP11-isolated-ea353c1-verdict.json` (redacted; grep for sk-/eyJ/Bearer: 0) |
| `make api-lint` | 0 | All checks passed (scope `apps/infrx-api`); `uvx ruff@0.15.12 check tests/integration/api_lifecycle` from `apps/infrx-api`: All checks passed |
| `make api-typecheck` | 0 | `pyright: 458 errors (baseline 458)` - no new error (the package is outside the pyright project) |
| `tests/integration/test_makefile_mutant_lists.py` without the wiring | 1 | 7 failed: `api-mutants names tests/integration/api_lifecycle/test_mutants.py 0 times` (expected until WR-AP11-1) |
| same with WR-AP11-1 applied in the worktree (reverted after) | 0 | 7 passed |
| `tests/integration/test_lab_package_isolation.py` with WR-AP11-2 applied (reverted after) | 0 | 2 passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (915 local links across 468 documents) |

No console-*/lab-* target: no app touched.

## Isolated run - what it is and is not

Real: PostgreSQL (Supabase image, migrations 0001-0059 + the PROVISIONAL Marlin seed), Valkey,
MinIO, the gateway and worker processes in pilot mode with LAB_CONTROL on, the controlled
engine `fake_vllm.py`, real HTTP. Declared fixtures (in the verdict): two verified individuals
with A1's grant and one consumer key each by SQL (AP-03's APIs absent), the seeded listing
(AP-06 absent), a NemoStation administrator membership and an outsider with HS256 sessions of
the world's GoTrue stand-in (AP-01 onboarding absent). Not a GPU, judge, hosted or complete
lifecycle result. An isolated world is rebuilt per invocation, so resume across invocations
is INVALID by design (target differs); resume is proved on the contract fake (layer 1).

## Wiring requests

- **WR-AP11-1 (Makefile)**: in `api-mutants`, after the lab_improve line:
  `\t# AP-11's lifecycle-runner list (api-lifecycle, LW7): layer 1 on the contract fake, no Docker`
  `\tcd $(CURDIR) && INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/api_lifecycle/test_mutants.py`;
  add `api-lifecycle` to `.PHONY`; append the target
  `api-lifecycle:` / `\tmkdir -p -m 700 $${TMPDIR:-/tmp}/infrx-ap11-state && $(API)/.venv/bin/python tests/integration/api_lifecycle/runner.py --mode isolated --world ap11 --state $${TMPDIR:-/tmp}/infrx-ap11-state/state-$$$$.json --out $(CURDIR)/research/plan/evidence/w7/AP11-raw-$(shell git rev-parse --short HEAD)`.
  Composed test: `tests/integration/test_makefile_mutant_lists.py` 7 passed with it.
- **WR-AP11-2 (tests/integration/test_lab_package_isolation.py)**: add to `CASES`
  `"api_lifecycle/test_runner.py::test_ap11_a_stage_without_its_api_is_blocked_naming_the_prerequisite",`.
  Composed test: the file's 2 cases pass with it.
- **WR-AP11-3 (tests/integration/api-lifecycle.sh, new, 0755)**: `set -euo pipefail`; cd to the
  repo root; a `mktemp -d` 0700 state dir removed on exit; `env -u AWS_ACCESS_KEY_ID -u
  AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN apps/infrx-api/.venv/bin/python
  tests/integration/api_lifecycle/runner.py --mode isolated --world ap11 --state
  "$state_dir/state.json" "$@"`, exiting with the runner's code. Composed test: `bash -n`;
  the same command produced the verdict above.
- **WR-AP11-4 (infra/runbooks/README.md)**: index row
  (a markdown link to api-lifecycle.md) ``| link — the API-only lifecycle runner: modes, resume, cleanup (AP-11) | — | tests/integration/api_lifecycle (layer 1 + isolated ap11) | live runs: AP-11 11c-11e, coordinator |``.
- **WR-AP11-5 (tests/integration/ENVIRONMENT.md, optional)**: a row for the wrapper (exit
  0/1/3/4, key ap11, verdict.json).

## Open items / notes for the coordinator

- Exit codes follow ENVIRONMENT.md (brief + coordinator note); verification.md's prose says
  "2 BLOCKED, 3 interrupted" - proposed ruling text: "AP-11's runner uses ENVIRONMENT.md's
  codes: 0 PASS, 1 FAIL, 3 BLOCKED/NOT RUN (an interrupted run is NOT RUN with a resume
  reason), 4 INVALID; verification.md's 2/3 wording is superseded."
- The isolated world binds loopback ports the kernel assigns (fake engine, edge, gateway,
  worker health), as PilotBox does for the worker; ap11's key has no port for them.
- 11c (batch 2) plugs in by setting a route's owner to None and giving the stage a `run`;
  AP-03's key/grant APIs replace the consumer fixtures (`state["minted"]` is the seam).

## Estimate (remaining AP-11 work)

This lane (11a/11b) is done pending review: 0.5 / 1 / 3 h (review fixes). 11c-11e (batches
2-3, live target, coordinator-serialized): 8 / 14 / 26 h, confidence low - basis: each
unblocked stage is ~1-2 h of stage code + defect oracles on this framework, plus the live
target's setup/cleanup and GPU-window scheduling that dominate the pessimistic case.
