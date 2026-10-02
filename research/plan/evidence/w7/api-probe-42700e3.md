# api-probe — AP-02 (operations): E4C run 2's code half (register rows 94/95)

- Lane `api-probe`, branch `codex/w7-api-probe`, worktree `.claude/worktrees/codex-w7-api-probe`, key `ap2` (PG `infrx-ap2-postgres`, 57552).
- Base `49114933`; code head `42700e37` (slice 1 `58d7de02`, slice 1 mutants `d1197368`, slice 2 `42700e37`); this file + the update JSON are committed after it.
- Read first: `research/plan/evidence/e/E4C-e6a8b40/README.md` (the 176 PoolTimeouts, 153 probe timeouts, 31 dependency-bound timeouts, the hosted settings).
- Nothing hosted, no box, no AWS/SSM/S3/Vercel, no secrets. No switch added; no default changed.

## Changed paths (all owned)

| Path | What |
|---|---|
| `apps/infrx-api/infrx/state/journal.py` | `READY_TIMEOUT_MS = 2000`, `READY_SQL`, `PgStreamStore.ready()`; `usage()` kept (journal-bytes gauge source, no longer the probe's body) |
| `apps/infrx-api/infrx/gateway/readiness.py` (new) | `Probe`, `PROBE_TIMEOUT_S`, `journal_check` (asks `ready()`), moved from `pilot.py` (pilot re-exports them after WR-PROBE-1) |
| `apps/infrx-api/tests/g/test_readiness.py` (new) | 6 functions / 9 cases: 4 unit, 3 PostgreSQL (ap2), 1 bounds order |
| `apps/infrx-api/tests/g/mutants.py` | 8 mutants; G's `Runner` gains `env=("INFRX_D_TASK",)` so the copy's PG cases run on the caller's key (otherwise they hit d1's default port) |

## Slice 1: the journal probe is one bounded, constant-cost statement

`READY_SQL` = `set local statement_timeout = 2000; select 1 from infrx.stream_chunks where job_id = '00000000-0000-0000-0000-000000000000' limit 1`. It goes as ONE simple-protocol message (psycopg sends a parameterless `execute` with `PQsendQuery`). The two statements run as one implicit transaction, so `set local` needs no session state, and the 2 s bound applies to the lookup (PG >= 13 re-arms the timer per statement). Hosted is PG 17.6. Locally this is proven on 16.14 by the stall case. The lookup is one btree descent on `stream_chunks_pkey` (EXPLAIN-asserted), so its cost does not depend on table size. The brief's example, `select 1 from infrx.stream_chunks limit 1`, would be a seq scan. `expire_journal` prunes the oldest rows, which sit at the front of the heap, so between vacuums that scan reads dead pages first. Both gateway logins can run it: `service_role`, and `infrx_runtime` through its 0021 SELECT grant plus the `runtime_reads` policy. No schema change.

- Red, recorded before the implementation: `INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/g/test_readiness.py` exit 2 (`ImportError: cannot import name 'readiness'`). On the base, `pilot.Probe(pilot.journal_check(s), timeout_s=0.5)()` returns `False` and asks `['usage']` when `usage()` is slow (one-off script, TimeoutError logged).
- Green: `INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/g/test_readiness.py` exit 0, **9 passed** with 0 skips. The PostgreSQL cases ran on ap2. The stall case holds `ACCESS EXCLUSIVE` on `stream_chunks` and gets `QueryCanceled` from the server in [1.5, 4) s, not the client's 8 s.
- Mutants (8, all **killed**, run one by one with `python -m tests.g.mutants <name>` on ap2 and again in the full run): `journal_probe_asks_usage`, `probe_fault_reads_ready`, `ready_bound_dropped`, `ready_bound_widened` (also names the bounds-order case), `ready_bound_session_wide`, `ready_scans_the_journal`, `ready_asks_usage`, `probe_bound_below_pool_wait`. Every new case is named by at least one.

## Slice 2: the bounds under a 30 s database stall (review; no default changed)

| Bound | Value (default / box) | What it does in a 30 s stall | Proposal |
|---|---|---|---|
| `database_pool_connect_timeout_s` | 5 s | Once the 6 pooled connections hold stalled statements, each new `getconn` waits 5 s, then `PoolTimeout`, then `OperationalError`. The lifecycle `_call` turns that into 503 `dependency_unavailable` (Retry-After 30). Those are the 176 log lines. | **Keep 5 s.** A longer wait queues more requests behind a saturated server and turns fast 503s into slow ones. It must stay below `PROBE_TIMEOUT_S - 2` (case `test_probe_bounds__…`). |
| `database_pool_statement_timeout_ms` (also the `infrx_runtime` role default) | 15 s | The server cancels each stalled statement at 15 s, which frees the slot. In a 30 s stall, slots cycle about twice. | **Keep.** It sits below `DEPENDENCY_BOUND_S`, so the server's refusal arrives first. |
| `DEPENDENCY_BOUND_S` (intake) | 20 s | Fires only when one call spends 5 s waiting for the pool plus more than 15 s running, or makes several statements. That is the 31 lines (503 Retry-After 5). | **Keep.** It is ordered above connect + statement on purpose (E3C s08). |
| `PROBE_TIMEOUT_S` | 10 s | **Before:** the probe waited up to 5 s for the pool, then ran `journal_usage()` (stalled up to 15 s), so it missed 10 s. That is the 153 lines. Every 5 s it also held one of the six connections for the whole stall. **After:** at most 5 s for the pool plus 2 s on the server, so it fails with a typed error by 7 s and holds a connection for at most 2 s. | **Keep 10 s.** It is now an outer guard, not the bound that fires. |
| `PROBE_EVERY_S` | 5 s | How often the probe runs. Readiness recovers within ≤ 5 s plus one probe after the stall ends. | **Keep.** The probe now costs one index descent. A longer cadence would only delay recovery. |
| `DATABASE_POOL_MAX_SIZE` | 10 default / **6 on the box** (gateway and worker each) | 12 of the hosted 60 `max_connections` through the pooler. A bigger pool sends more concurrent work into a CPU-starved instance and makes the stall longer. | **Keep 6** until the compute class is sized (operator: Medium now, Large after launch) and `71-pool-budget.sh` is re-run with the pooler's limits. Re-evaluate on the new compute with `SET=DATABASE_POOL_MAX_SIZE=<n>`. |

**Ruling request** (the coordinator numbers it): *The gateway's readiness probes are constant-cost statements bounded on the server: the journal probe is `PgStreamStore.ready()`, a primary-key lookup under `set local statement_timeout = 2000`, never `journal_usage()`. The pool's connect timeout plus the probe's server bound stays strictly below `PROBE_TIMEOUT_S`, and the server bound stays below the statement timeout (`test_probe_bounds__a_pool_wait_plus_the_server_bound_fits_inside_the_probe_bound`). A change to any of them is a ruling with a case.* No default changes in this lane.

Consequence: during a stall that exhausts the pool, readiness still reads `unavailable`, and admission still answers 503. That is correct, because the database cannot admit then. What changes is that the probe no longer adds an aggregate load every 5 s, and no longer pins a connection for 15 s while the database is slow. Proving that this ends the soak's 503 episodes needs a new qualifying run after the compute is resized.

## Slice 3: `infra/rollout/steps/75-pg-activity-sampler.sh`: BLOCKED

Claude Code's auto-mode classifier denied writing the step file ("[Production Reads]"): a background `pg_stat_activity` reader of the hosted database run on the box. Per the denial, nothing was written and no other path was tried. No `tests/i/test_rollout.py` cases were added, so `tests/i/mutants.py` needs no new entry. Needs: the user/operator allows the lane (or the coordinator) to author this step. The brief's design stands: START/STOP, `RUN` required, the gateway image, `--env-file`, every 10 s, `/opt/dlami/nvme/e4b/<RUN>/pg-activity.jsonl`, `bash -n` plus a no-secret / refuses-without-RUN case.

## Slice 4: `models/marlin2b/results/E4B-protocol.md` driver-lag note (row 95): BLOCKED

The classifier denied reading the protocol file ("[Production Reads]"), so the amendment could not be placed. Text for the coordinator to append as the next amendment (a protocol note, not a widened bound): *"The overload cell's client must not share the box's CPUs with the gateway, worker and engine. Run it from another host through the public edge (or with pre-encoded bodies). A cell INVALID for driver lag above `max_driver_lag_s` (1.0 s) is rerun that way, never judged with a widened bound (E4C runs 1 and 2: 1.020 s, 1.073 s)."* `models/marlin2b/bench.py` was not touched.

## WIRING REQUEST WR-PROBE-1 (coordinator: `pilot.py` + test fakes)

Patch: `research/plan/evidence/w7/api-probe-wr/WR-PROBE-1-42700e3.patch`. It applies cleanly to `42700e37` (`git apply --check`) and touches 12 files, +39/−75:
1. `infrx/gateway/pilot.py`: delete `PROBE_TIMEOUT_S`, the `Probe` class and `journal_check`, plus the now-unused `ThreadPoolExecutor` import. Add `from .readiness import PROBE_TIMEOUT_S, Probe, journal_check  # noqa: F401 - WR-PROBE-1`. Update the module docstring line for the probes. `_refresh` and `PROBE_EVERY_S` stay.
2. The test fakes that stood in for the probe's body move from `usage` to `ready`. In `tests/g/test_startup.py` (7 sites), `tests/ap02|ap03|ap07/test_composed.py`, `tests/contracts/test_config_and_imports.py` and `tests/integration/lab_rollout/scenarios_route.py`, `stream.usage = lambda: asyncio.sleep(0, {})` becomes `stream.ready = lambda: asyncio.sleep(0, True)`. In `tests/g/test_composition.py`, `tests/m/test_pilot_media.py` and `tests/g/jobs/test_jobs.py`, `Journal.usage` becomes `Journal.ready`. `contracts/fakes/state.py` is deliberately not changed: adding `ready()` there breaks `test_lab_existing_frozen_contracts_are_byte_identical` (tried, then reverted).
3. Composed test: `tests/g/test_readiness.py::test_probe_wiring__the_pilot_composes_the_journal_probe_on_ready`. It checks that `pilot.Probe/journal_check/PROBE_TIMEOUT_S` are `readiness`'s objects, and that the composed journal probe answers from `ready()` while `usage()` hangs. It joins `journal_probe_asks_usage`'s cases in `tests/g/mutants.py` and is **killed** there with the patch applied. The build-path proof is the existing `test_f_base__readiness_probes_are_cached_answers_the_lifetime_refreshes` with its `Journal.ready` fake.
- Results with the patch applied:
  - `INFRX_D_TASK=ap2 pytest tests/g/test_readiness.py tests/g/test_composition.py tests/g/test_startup.py tests/m/test_pilot_media.py tests/g/jobs/test_jobs.py tests/ap02/test_composed.py tests/ap03/test_composed.py tests/ap07/test_composed.py tests/contracts/test_config_and_imports.py tests/contracts/lab/test_lab_contracts.py`: **601 passed**.
  - Wide run `tests/g tests/m tests/contracts tests/w tests/ap02 tests/ap03 tests/ap07` (mutant files excluded): **3475 passed, 85 skipped, 1 failed**. The failure, `tests/ap02/test_parity_pg.py`, comes from `apps/app` node_modules not being installed in this worktree (`Cannot find package '@infrx/api-client'`). After `pnpm --dir apps/app install --frozen-lockfile --offline` it is **1 passed**.
  - `tests/integration/lab_rollout/scenarios_route.py` was edited mechanically and not run (the lab-rollout gate needs its services).

## Checks (exit codes)

| Command | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/g/test_readiness.py tests/g/test_startup.py tests/i/test_rollout.py` | 0 | 80 passed |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright **457** errors (baseline 458; none new) |
| `uv run --frozen pytest -q tests/g/test_mutants.py -k "well_formed or every_case"` | 0 | 2 passed |
| `INFRX_D_TASK=ap2 INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/test_mutants.py` | 1 | 499 passed, **16 failed. All 16 are pre-existing `misdeclared` anchors in files this lane did not touch:** `composition_root_*` ×14 (`gateway/app.py` `ROUTERS = (health, models, ingress, uploads, jobs, feedback,` no longer present), `given_stores_replaced` (`gateway/pilot.py` anchor), `unapproved_card_published` (`gateway/routes/models.py` anchor). All 8 of this lane's mutants are killed. The 16 belong to whichever lane last moved `ROUTERS`/`pilot.py`/`models.py` (coordinator triage). |

Note: during the full mutant run, the temporary WR-patch tree also used ap2 once, and one composed run showed 3 transient failures from the shared lock. Rerun alone: 601 passed. The full mutant run's 16 failures are anchor-count failures, which do not depend on PostgreSQL.

## Open / remaining

- Slices 3 and 4 are BLOCKED on the classifier (see above).
- WR-PROBE-1 is to be merged by the coordinator.
- The 16 pre-existing misdeclared G mutants need an owner (not this lane's paths).
- Proving the probe change on the hosted database is a new qualifying E4C run after the compute resize (coordinator/operator).

Estimate for the remaining lane work (sampler step + tests + mutants + protocol note once permitted; WR merge): optimistic 1.5 h, likely 3 h, pessimistic 5 h; confidence medium. Basis: slices 1–2 took about 3 h including the 34-min mutant run; the sampler is one shell step with two cases, like `71-pool-budget.sh` and its tests.
