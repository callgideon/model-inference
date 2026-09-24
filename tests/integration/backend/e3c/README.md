# E3C — BACKEND-LOCAL: the corrective scenario matrix on real services

```bash
make api-env                                                        # once
apps/infrx-api/.venv/bin/python tests/integration/backend/e3c/runner.py --out <dir>
#   [--keep] [--reuse] [--only s03,s04] [-k EXPR] [--control nc-admission-ready=<tree>]
#   [--s3-image pgsty/minio@sha256:...]     # WR-2: the pinned quay MinIO digest answers 401
```

Writes `<dir>/verdict.json` and exits like E2C's gates: `0` PASS, `1` FAIL, `3` BLOCKED or
NOT RUN, `4` INVALID. The gate is the worst scenario or negative control
(FAIL > INVALID > BLOCKED > NOT RUN > PASS).

**What it proves, and what it does not.** Orchestration on real PostgreSQL (every migration),
PostgREST, Valkey and an S3-compatible store, with real gateway, worker, collector and
operator-CLI processes, and E2's controlled protocol engine (`tests/integration/fake_vllm.py`).
It proves nothing about Marlin quality, GPU capacity or hosted behaviour.

## Isolation

Namespace `e3c` (tasklocal block 56900–56999): containers `infrx-e3c-*`, PostgREST
`infrx-e3crest*`, database `infrx_e3c` and its clones, Valkey prefix `infrx_e3c:`, bucket
`infrx-e3c`, each box under its own object prefix. Faults are real: a box process is held at
a named step of the real code path (`world.POINTS`) and SIGKILLed by process group, only
processes the scenario's own `Box` started; container faults go through `harness.Faults`,
namespace-checked. Nothing is killed by name. No hosted DB, box or AWS.

## Files

| File | What |
|---|---|
| `runner.py` | provision (E2's `run.py` stages), pytest over `scenarios_*.py`, classify, verdict, teardown |
| `world.py` | the composed stack (E3B's `stack`/`pilotbox` reused), the `Box` process entrypoints, fault points, bypasses (negative controls), the collector process, scenario helpers |
| `scenarios_*.py` | the matrix; named outside `test_*` so `pytest tests/integration` never collects a red scenario |
| `test_e3c_runner.py` | s12 (VERIFY-REPRO): the verdict's own rules, no stack needed |
| `conftest.py` | per-case work directory under `<out>/cases/` (box logs, markers) |

## Vocabulary

A case skips `BLOCKED[<lanes>]` when the interface it drives is not in the tree,
`INVALID[...]` when it could not establish its premise; any other skip is NOT RUN. None is a
pass. A negative control (`test_nc_<oracle>__<scenario>_…`) counts only over a scenario that
passed in the same run; a revert-type control (`--control NC=<tree>`) runs the scenario from a
scratch tree with the lane's fix reverted and expects FAIL.
