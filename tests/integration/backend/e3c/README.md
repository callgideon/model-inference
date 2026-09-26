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
| `reverts.py` | the SQL revert-type controls: one check removed by a last migration on a scratch tree |
| `control_trees.sh` | builds every revert-type control's scratch tree from a commit (`--control` arguments) |
| `conftest.py` | per-case work directory under `<out>/cases/` (box logs, markers) |

## Vocabulary

A case skips `BLOCKED[<lanes>]` when the interface it drives is not in the tree,
`INVALID[...]` when it could not establish its premise; any other skip is NOT RUN. None is a
pass. A negative control (`test_nc_<oracle>__<scenario>_…`) counts only over a scenario that
passed in the same run; a revert-type control (`--control NC=<tree>`) runs the scenario from a
scratch tree with the lane's fix reverted and expects FAIL.

## The App gate's cells (E3C-CELLS)

`tests/integration/app/runner.py` delegates three 04-verification oracles the browser journey
cannot exercise to one scenario each; each has a revert-type control (`reverts.py`) whose
reverted tree must turn the scenario red.

| Cell | Scenario | Injection | Oracle | Control (what the scratch tree reverts) |
|---|---|---|---|---|
| DUR-FENCE | s14 (`scenarios_fence.py`) | a worker held right after its claim; its lease lapses; generation 2 claimed by another process or by the held process's own second runner | generation 1's token refused at append, renew, load_work, a second inference/preparation claim and settle; one settlement, generation 2's output only, one engine generation | `nc-dur-fence`: 0016 `fence_lease`'s generation check |
| DUR-CAP | s15 (`scenarios_capacity.py`) | 16 admissions on 4 keys in 2 orgs through 2 gateways at MAX_ACTIVE_JOBS 4 / org 3 / key 2; a wallet funded for 2.5 holds bursting with an operator debit racing | exactly the cap admitted, every scope within its cap, 429 / 402 refusals holding nothing, available never negative, no deadlock | `nc-dur-cap`: 0011 `admission_checks`'s cap comparisons off by one |
| CREDIT-RATE | s16 (`scenarios_rates.py`) | `publish-card`, then a new serving + deployment revision, while one job runs (held after its first chunk) and one waits; gateway restarted at each card | every job settles at its admitted revision and card; a gateway still at the old card admits nothing; unknown / private / unpriced refused at admission | `nc-credit-rate`: 0018 `terminalize` debits at the current listing's card |
