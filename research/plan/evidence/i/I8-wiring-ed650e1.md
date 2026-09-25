# I8 wiring requests applied - codex/i8-wiring

Base `63011a31` (claude/consumer-v1, I8 merged); code head `ed650e1c`. Nothing touched the
box, AWS, SSM, S3 or hosted Supabase. Services: the i8 stand-in only (postgres 55450, pgbouncer 55496).

| Commit | Request | Change |
|---|---|---|
| `a7ff1832` | port | `tests/i/pooler.py` stand-in PgBouncer 55477 -> 55496 (55477 is d6's harness decoy); `test_observe.py` bad-port swap follows |
| `7a226e7d` | WR-I8-1 | `jobstore.session_state_allowed(dsn)` (port != `TRANSACTION_POOLER_PORT` 6543); `jobstore.connector` connects with `prepare_threshold=None` and sets the role only off 6543; `pilot.configure_connection(ms, *, session_state=True)` sends nothing without it; `pilot.connection_pool` passes `session_state_allowed(DATABASE_URL)`, pool `kwargs` and probe connection get `prepare_threshold=None`. The characterization `…breaks_on_the_transaction_pooler_today` is rewritten as `…the_composed_runtime_pool_holds_on_the_transaction_pooler` (pool and connector, 12 alternating servers, one identity `infrx_i8_login` and its role-level `15s` timeout), plus `…on_6543_the_runtime_sends_no_session_set` (no docker). `tests/g/test_composition.py` probe kwargs updated; the g mutant `pool_statement_timeout_zero` re-anchored to the split line. `KNOWN_SESSION_SET` unchanged: the three SETs still exist but now run only off 6543 |
| `886b9ca2` | WR-I8-2 | `metrics.py` families `infrx_db_pool_connections{state=size,available,max}`, `_requests_waiting` (gauges), `_requests_total`, `_wait_seconds_total`, `_timeouts_total` (requests_errors), `_connection_errors_total`, `_connections_lost_total` (counters, pop deltas accumulated); `record_pool(reg, pool.pop_stats())` at scrape in `observe/route.py` (from `rt.lifetime.pool`) and `WorkerService` (new `pool=` field, passed by worker main). `operations.json` drops the three db_pool names from `pending_producers`; `test_observe` counts `record_pool` as a runtime producer. New case `…both_processes_export_their_db_pool_at_scrape` |
| `e514841a` | WR-I8-4 | `observe/route.py` `collect_host(rt.metrics, disks, gpu=False)`; new case `…the_gateway_exports_no_gpu_gauge` |
| `77a02b14` | WR-I8-5 | `serving-version.json` `model.processor_config_digest` / `preprocessor_config_digest` = **null** with `processor_digests_todo`. The pinned revision is not on this host (no HF cache, nothing under /mnt/nvme or /opt/dlami/nvme) and was not downloaded. The repo copies' sha256 (`d89ef49c…` / `27225450…`) are recorded there as est. candidates only. W-side producer: `measure/inventory.sh` now hashes both served files. `artifacts.py PINNED` unchanged |
| `ed650e1c` | mutants | 8 new I mutants (pool_prepares_again, pool_sets_session_state_on_6543, connector_sets_role_on_6543, connector_prepares, pool_wait_in_ms, gateway_scrape_skips_pool, worker_scrape_skips_pool, gateway_collects_gpu); `COMPOSED` renamed; `stand_in_pooler_replays_prepares` keeps only its direct case |

Not applied (by instruction): WR-I8-3 (G7), WR-I8-6 (D10).

## Commands (`apps/infrx-api`, `INFRX_D_TASK=i8`)

| Command | Exit | Result |
|---|---|---|
| new cases against the 63011a31 `pilot.py`/`jobstore.py` | 1 | on_6543: hook SETs on 6543; composed: `PoolTimeout` (the hook's `set role` refused for the login). no_gpu against the old route: 1 failed |
| `pytest -q tests/i/test_pooler.py tests/g/test_composition.py` | 0 | 34 passed |
| `pytest -q tests/w/test_worker_main.py tests/w/test_service.py tests/contracts tests/i/test_worker_unit.py` | 0 | 1298 passed, 4 skipped |
| `pytest -q tests/w/test_serving.py tests/w/test_w4.py tests/g/ops/test_publication.py tests/i/test_artifacts.py tests/i/test_prereqs.py tests/i/test_packaging.py ../../tests/integration/backend/test_endpoint_doc.py` | 0 | 85 passed |
| `pytest -q -rxXs tests/i tests/g --ignore=tests/g/ops` at `77a02b14` (detached, pid 483521) | 1 | 737 passed, 1 xfailed (F4), 2 failed: `tests/i/test_mutants.py` list checks (renamed case, 4 unmutated cases), fixed in `ed650e1c` |
| `python tests/i/mutants.py <11 names>` | 0 | 11/11 killed (the 8 new + the 3 touched pooler mutants; first run broken_runner: another lane's suite held the i8 flock; one KeyError death and one survivor fixed in `ed650e1c`) |
| `python -m tests.g.mutants pool_statement_timeout_zero pool_configure_dropped` | 0 | 2/2 killed |
| `pytest -q tests/i/test_mutants.py tests/i/test_pooler.py` at `ed650e1c` | 0 | 59 passed |

## Open

- Coordinator follow-up: `infrx/contracts/tasklocal.py` has no row for the i8 pgbouncer stand-in
  (i8 = postgres 55450, valkey 55495 only); 55496 is hard-coded in `tests/i/pooler.py`. Not changed here.
- WR-I8-5 digests: measure on the serving host (`measure/inventory.sh` served-bytes section), fill both
  fields, then add both to `artifacts.py PINNED` (I8).
- The GpuUnavailable `overrides` entry in `operations.json` can now go (I8 follow-up; harmless meanwhile).
- The db_pool dashboard panels (WR-I8-2's last step) are not added.
- Other lanes' full suites run `tests/i` and share the i8 flock/containers: concurrent runs block each other.
