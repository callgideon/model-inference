# I2B-R4 — the worker process entry point `python -m infrx.worker`

| Field | Value |
|---|---|
| Task | **I2B-R4** (WORKER lane): the worker PROCESS the pilot's `infrx-worker.service` runs, which existed on no branch and was the rollout preflight's only in-image refusal (`ROLLOUT-PREP-2026-09-23.md`, request 2) |
| Status | **implemented** — the composition root, the unit/installer/rehearsal alignment, E3B's pilot box on the real entry point and rc08b's body. **Not integrated, not deployed**: nothing ran on the pilot box, AWS or hosted Supabase |
| Owner/session | Opus 5.5 implementation session, 2026-09-23/24 |
| Base SHA | `08ed293` (the E3B phase-3 head `1fa825b` + the cutover's final head `a1e88dc`) |
| Merged in | `origin/codex/e3b-phase3-bodies` **`b9529d1`** (the phase-3 lane's final head), `--no-ff`, merge commit `07fe76e` (the coordinator's instruction); ROLLOUT-PREP's `a670ec6` (rehearse.sh step 8) cherry-picked as `723a50c` |
| Implementation SHA | **`fb8babb`** (this report is committed after it) |
| Branch / worktree | `codex/i2b-r4-worker` in `.claude/worktrees/codex-worker` |
| Oracles | `BACKEND-DEPLOY`, `DEPLOY-FAILCLOSED`, `OPS-RECOVER` |

## What was built

**`apps/infrx-api/infrx/worker/__main__.py`** — the composition root, from the settings the
gateway reads and with the gateway's own helpers (`gateway.pilot.connection_pool`,
`object_store`, `valkey_index`, `build_info`), so the two processes cannot disagree about a
store:

* **fail closed.** `validate_runtime` first (an unset/unknown `INFRX_MODE`, a pilot without
  its identity or metering settings, a bad deployment value), then what this process needs in
  every mode: `DATABASE_URL` (D's `PgJobStore` + `PgStreamStore` on ONE pool - never an
  in-memory store), `VALKEY_URL`, `PROCESSING_CACHE_DIR` (absolute, a readable directory) and
  `S3_MEDIA_BUCKET` (HeadBucket, as the gateway). A refusal prints setting names, never a
  value, and exits **2** before a listener is bound or a job claimed; a pool that does not
  open is the same refusal (`DATABASE_URL did not answer`).
* **M's request / E3B IR3F-5:** `local_uri = MediaPreparation(pilot.object_store(settings),
  cache=ProcessingCache(PROCESSING_CACHE_DIR)).local_uri` - exactly what `pilotbox.worker()`
  composed at `40d9105`.
* **W request 5 (D5):** `CreditWork` (moved here from the pilot box) routes the runner's
  `load_work`/`complete` to `load_work_credit`/`complete_credit` in the CREDIT regime.
* `VllmEngine` on `UPSTREAM` (served model `marlin2b`, serve.sh's), W2's runner
  (`count_prompt_tokens` = preparation's stored count, `put_result` = D's), W2's loop on Q2's
  index, W3's `WorkerService` with `WORKER_CONCURRENCY`.
* **Readiness and metrics** on `127.0.0.1:WORKER_HEALTH_PORT` - a new 08 §5.1 setting,
  default **8002** (what `lib.sh wait_ready` and `60-verify-local.sh` already probe; W3 request
  6), read by the runtime and never written by the installer (`preflight.NOT_SETTABLE`). W3's
  listener gains `GET /metrics` (the process's `Registry("worker")`: `infrx_build_info{revision,
  image}` from `INFRX_RELEASE_SHA`/`INFRX_IMAGE`; a pilot refuses without them). It is bound
  only after the pool opened, the bucket answered and the cache directory was found;
  `/readyz` is then 200 only while the engine's `/health` answers and the pool runs (W3).
* **SIGTERM/SIGINT:** W3's `serve()` drain (stop claiming, finish in-flight inside the
  generation budget, release the rest), then exit 0; a runner or the reaper that died exits 1.

**Unit / installer (item 2).** `infrx-worker.service` already runs `python -m infrx.worker`
on the host network with the env file and the read-only media mount - unchanged except its
comment (the refusal, the port). `lib.sh`'s comment names the setting;
`preflight.NOT_SETTABLE["WORKER_HEALTH_PORT"]`; 08 §5.1 row + verification-log line; the
frozen deployment-name table in `tests/contracts/test_config_and_imports.py` gains the row.
**`rehearse.sh`** (I's file, the minimal change that makes it pass on this tree): step 0
starts the box's own PostgreSQL (the pinned supabase/postgres, migrated 0001-0018 by
`migrate.py`) and MinIO (the compose file's pinned image) in the box's network namespace;
`DATABASE_URL` arrives through the SSM stub (`pg_journal_url`), `S3_MEDIA_BUCKET` +
`S3_ENDPOINT_URL` through `INFRX_SET`, the MinIO credentials through the docker wrapper (the
instance role's stand-in; local literals). Valkey is the existing step-4 unit (the gateway's
index connects lazily). Step 2/5: the shared legacy key is now **401 `invalid_api_key` from
the ingress** (R51/R86: a 200 would be the unmetered legacy route) and the edge checks read
`GET /v1/models` and the ingress's 401 through Caddy. Step 3: the in-image **pilot probe now
passes** (ingress composed, worker entry present); the pilot install drill is still refused
(no bucket, 29 GiB free vs the 60 GiB budget). New **step 4b**: the worker unit on the dev env
file - `/readyz` 200 on 8002, the build gauge on `/metrics`, uid 10002 / read-only / no caps,
drain on `systemctl stop`. Step 8 is ROLLOUT-PREP's `a670ec6`, byte-identical.

**E3B's pilot box (item 3), reconciled with the phase-3 lane's `40d9105`.** The box's worker
process IS `python -m infrx.worker`: the lane's in-test composition (`worker()`,
`worker_service`, `CreditWork`, `Wall`) is deleted - one composition, the product's. The box
gives it `UPSTREAM` (the fake vLLM) and a `WORKER_HEALTH_PORT` of its own and `start` waits for
its `/readyz`; the index namespace is the pilot's (`infrx:sched:{pilot}`, where the real
worker reads; `close` removes it); both box processes resolve `infrx` from an inherited
`PYTHONPATH` first (E's mutation runner's copy) and the checkout's API root after it. **rc08b**
(I3B's drill, the phase-3 gate's only remaining PENDING) has its body: SIGTERM to the worker
process mid-attempt → exit 0 well inside the unit's stop budget, the job
`succeeded/completed/settled` with one debit and no attempt unreleased, the drain record in its
log. `recoverykit.OWNERS` is empty (`I2B-R4` retired), rc00 pins no pending drill, `i3bm57`
(rc08b's structural probe) retired, `i3bm117` added. The first form of item 3 (`41a7480`, an
opt-in `INFRX_E3B_REAL_WORKER` flag) is superseded by `1294d33`; `245f5af` put the TEST
process's `infrx` first on the box's path, which made six E3B journey mutants survive the
gate (the journey's test process imports the checkout's package: `fake_vllm.py` inserts the
real API root at `sys.path[0]`), fixed in `fb8babb`.

## Per item: commit → case → mutant (kill text, measured with the runner's own copy)

Cases are `tests/w/test_worker_main.py` (21 items) unless named otherwise. The lists:
`tests/w/worker_main_mutants.py` (13 service-free `MUTANTS` + 4 `PG_MUTANTS`, through the
shared runner; `test_worker_main_mutants.py`, added to `make api-mutants`), 3 in `tests/i`'s
list, `i3bm117` in I3B's.

| Item | Commit(s) | Case | Mutant → death line |
|---|---|---|---|
| 1 composition | `c29193d`, `2795016`, `ece97dd` | `each_missing_setting_refuses_startup_by_name` (11 params) | `main_validate_runtime_skipped`, `main_database_url_not_required`, `main_cache_dir_unchecked`, `main_store_defaulted_to_in_memory` (the object store: `InMemoryObjectStore()` when no bucket) → `test_worker_main.py:155: Failed: DID NOT RAISE <class 'infrx.config.RuntimeMisconfigured'>` (`[DATABASE_URL]`, `[S3_MEDIA_BUCKET]`, ...) |
| 1 | same | `the_composition_is_the_pilots_stores_and_settings` | `main_credit_work_absent` → `:180: AssertionError: assert (False)` (not a `CreditWork`) |
| 1 local_uri | same | `local_uri_finds_the_file_the_gateway_prepared` | `main_local_uri_without_the_shared_cache` → `infrx/media/prepare.py:476: NotFound: not_found: media med_1 is not in the processing cache` (a typed `DomainError`: an honest death under R83) |
| 1 build info | same | `build_info_is_the_installed_settings_never_git` | `main_build_info_from_git` (in `gateway/pilot.py`) → `:224: AssertionError: infrx_build_info{process="worker",revision="other",...} 1.0` |
| 1 readiness | same | `readyz_waits_for_the_engine_and_metrics_are_served` | `main_readiness_before_the_engine_check` (`service.py`) → `:254: AssertionError: (200, '{"ready": true, "live": true, "engine": "down", ...')`; `main_metrics_not_served` → `:256: AssertionError: (404, '{"error": "not_found"}')` |
| 1 refusal | same | `the_process_refuses_to_start_naming_the_setting` (subprocess) | `main_refusal_exits_zero` → `:296: AssertionError: infrx.worker: refusing to start: INFRX_MODE='dev': requires DATABASE_URL, VALKEY_URL, PROCESSING_CACHE_DIR` (exit 0) |
| 1 round trip (PG) | same | `_pg__a_job_the_gateway_admitted_runs_in_the_worker_process` | `main_credit_work_absent_on_postgresql` → `:512: AssertionError: (None, '...` (the job never terminal) |
| 1 drain (PG) | same | `_pg__sigterm_drains_the_in_flight_job_and_exits_0` | `main_drain_skipped_on_sigterm` (serve → start + wait forever) → `:554: AssertionError` (the process died by the signal, not 0) |
| 1 pool (PG) | same | `_pg__an_unreachable_database_refuses_before_readiness` | `main_pool_not_opened` → `:587: AssertionError: (1, "...` (served, died on the index, never refused 2) |
| 2 port | `0d75d8a` | `tests/i/test_worker_unit.py::..._the_worker_readiness_port_is_one_value_the_installer_never_writes` | `worker_port_settable` → `test_worker_unit.py:33: assert 'WORKER_HEALTH_PORT' in {...}`; `worker_ready_port_drift` (lib.sh 8003) → `:31`; `worker_port_default_drift` (config 8003) → `:28: assert (8003 == 8002)` |
| 2 pilot image | `d6da8a6` | `tests/i/test_packaging.py::..._a_pilot_image_must_be_unprivileged_and_carry_the_worker` (now: the entry point is present; a runtime without it is refused) | I's existing `root_runtime_accepted`, `worker_entries_unchecked` → killed (`1 failed, 21 deselected`) |
| 2 rehearsal | `d6da8a6`, `723a50c` | `rehearse.sh` run for real (below) | - (a bash rehearsal on containers; its checks are the proof) |
| 3 pilot box | `1294d33`, `fb8babb` | `the_pilot_box_runs_the_real_entry_point` | `pilotbox_worker_emulated` → `:309: AssertionError: assert (['/home/rey/...0.1:1/readyz') == ...`; `pilotbox_worker_imports_the_checkout` → `:316: AssertionError: assert '/tmp/claude-...mutation-copy' == '/a-mutation-...pps/infrx-api'`; `pilotbox_worker_private_namespace` → `:318: AssertionError: assert 'infrx_e2:{e3b3}' == 'infrx:sched:{pilot}'` |
| 3 pilot box (PG) | same | `_pg__the_pilot_box_starts_the_real_worker_and_waits_for_it` | `pilotbox_worker_not_awaited` → `:603: AssertionError: ((0, 'ConnectionRefusedError'), '')` |
| 3 rc08b | `1294d33` | `recovery/test_recovery.py::test_i3b_rc08b_a_sigterm_drain_of_the_worker_process_finishes_its_attempt_and_exits_0` (e3b2 stack) | `i3bm117` (the same serve → start edit) → `test_recovery.py:816: AssertionError` |
| 3 journeys on the real worker | `fb8babb` | the nine journey cells, the resume, dr11, rc03 (e3b2 stack) | E3B's `e3bm64` → `test_journey.py:82`, `e3bm70` → `:85`, `e3bm71` → `:246`, `e3bm74` → `:74`, `e3bm75` → `:74`, `e3bm78` → `:244` (all `AssertionError`, `1 failed, 14 deselected`) |

## Runs (UTC 2026-09-23/24; env names only; `$SC` = the coordinator scratchpad's `i2br4/`)

| # | Command | At | Exit | Tail |
|---|---|---|---|---|
| R1 | `UV_OFFLINE=1 make api-env` | `08ed293` | 0 | synced |
| R2 | `pytest tests/w/test_worker_main.py` with `INFRX_D_TASK=d4 INFRX_D2_VALKEY_PORT=55465 INFRX_D2_VALKEY_CONTAINER=infrx-worker-valkey INFRX_M_S3_ENDPOINT=http://127.0.0.1:55781 INFRX_M_S3_LOCAL_CREDS=1` | `fb8babb` | 0 | `21 passed` (inside R9) |
| R3 | the subprocess round trip alone (`-k _pg__`) | `c29193d`+ | 0 | first run: `1 passed` round trip; the drain's record assertion corrected ("2 finished" = both runners); then `18 passed in 13.40s` |
| R4 | `INFRX_MUTANTS=all pytest tests/w/test_worker_main_mutants.py` (same env) | `2795016` / `41a7480` / `245f5af` / **`fb8babb`** | 0 / 0 / 0 / **0** | `18 passed` / `21 passed` / `21 passed` / **`22 passed in 119.19s`: 17 of 17 mutants killed** (logs `f6894671`, `fc3328e4`, `461fd7ac`, `fd1985dd`) |
| R4b | the same at `fb8babb`, first attempt (with the cases in one pytest) | `fb8babb` | 1 | `4 failed, 40 passed`: the 4 PG mutants `broken_runner` - the PG list's pristine baseline was red on the drain case once (cause not captured by the runner); the baseline alone then passed 3/3 (`4 passed` each) and R4 above passed (`87afe423`) |
| R5 | `make api-test` equivalent: `uv run --frozen pytest -q` in `apps/infrx-api`, the R2 env + `INFRX_Q_VALKEY_PORT=55494`, `PYTEST_ADDOPTS=-rfEs` | `245f5af` | 0 | **`3593 passed, 2 skipped, 5 xfailed in 1821.07s`** (the 2 skips: the two PG mutant parametrizations empty by default) (`e5e944fb`) |
| R6 | `tests/i/mutants.py worker_port_settable worker_ready_port_drift worker_port_default_drift`; `... root_runtime_accepted worker_entries_unchecked` | `0d75d8a`, `fb8babb` | 0 | `3/3 killed`; `2/2 killed` |
| R7 | **`deploy/rehearse.sh`** (`REHEARSAL_NS=infrx-wrk`) | `d6da8a6` | 0 | **`REHEARSAL PASSED`, 48 PASS / 0 FAIL**, `teardown: nothing infrx-wrk-* left` (`b0b18e4a`); the runtime image is the same at `fb8babb` (no change under `infrx/`, `deploy/`, `openrouter/` or the migrations since) - R12 re-runs it there |
| R8 | e3b2 stack: `INFRX_E2_NAMESPACE=e3b2 run.py --layer 2 --keep` (twice: before and after `fb8babb`) | | 0 | preflight/services/migrate/rls PASS (731 cases) |
| R9 | on it: `pytest tests/integration/backend` (the real worker in every box) | `245f5af`'s tree (uncommitted then) | 1, then **0** | first: `130 passed, 1 failed, 10 errors` - every one `fake vLLM ... 56780: address already in use` (P-21: a TIME-WAIT client socket `127.0.0.1:56780 -> :55435`); rerun: **`141 passed, 3 skipped`** (the 3: the stage's PostgREST, as phase 3) (`fc0508fb`); rc03 + rc08b + rc00 alone `3 passed` (`878b3536`) |
| R10 | `mutants_i3b.py --layer all --only i3bm117 --only i3bm99` (and `i3bm117` again at `fb8babb`); `mutants.py --layer all --only e3bm64/74/70/71/75/78` (private TMPDIR, `INFRX_E2_STATE_FILE` pointed back) | `fb8babb` | 0 | `i3bm117 killed`, `i3bm99 killed`; the six e3bm `killed` (death lines in the table) |
| R11a | **layer-3 gate** `INFRX_E2_NAMESPACE=e3b2 INFRX_Q_VALKEY_PORT=55494 run.py --layer 3 --canary --only-suites` | `245f5af` | 1 | backend **144 passed / 0 pending / 0 failed** (the first run without the I2B-R4 pending); mutants 252: **6 not killed** (`e3bm64/70/71/74/75/78` - the PYTHONPATH defect, fixed in `fb8babb`); suites: 3 failed + 10 errors, all P-21 `address already in use` (`49239ed0`) |
| R11b | **the gate again** | **`fb8babb`** (clean start and end) | 1 | preflight/services/migrate/rls/engine/canary/teardown PASS; **backend PASS `{passed 144, pending 0, failed 0}`, stale none**; **mutants PASS `{252, killed 248, controls_survived 4, not_killed 0, problems null}`**; suites FAIL on ONE case, `test_fake_vllm.py::test_killing_the_engine_process_is_a_transport_failure_and_it_restarts` - `56782: address already in use` (P-21); that file re-run 3x after: `17 passed` each. 1166.7 s, 0 e3b2 containers after (`c220c937`) |
| R12 | `deploy/rehearse.sh` at the implementation head | `fb8babb` | 0 | **`REHEARSAL PASSED`, 48 PASS / 0 FAIL** (release `fb8babb`; runtime image `sha256:bc2477f5a282…`, the same id R7 built), the pilot probe `{"ok": true, ..., "problems": []}`, step 4b `drained: 10 finished, 0 released`, `teardown: nothing infrx-wrk-* left` (`44b8ea37`) |
| R13 | layer 0 of the backend tree, no stack | `1294d33` | 0 | `65 passed, 79 skipped` (base `08ed293`: `64 passed, 1 failed` - test_stage on M3-U1, fixed by the merge) |

## Limits

1. **No product process prepares a job.** Every admission lands `preparing` with a
   `prepare_dispatch`; nothing but the pilot box's emulation (and this lane's tests) claims a
   preparation lease and calls `prepared(..., prompt_tokens=)` - and preparation's exact
   prompt count has no producer (no tokenizer; W2 limit 2 / request 3). So the pilot's
   gateway + this worker serve no job by themselves. The round trip here emulates
   preparation exactly as the pilot box does (the fake engine's 1200 tokens), named as such.
2. **A CREDIT deployment runs every job through the CREDIT doors**: a legacy job still in
   flight at a regime switch is `not_found` at `load_work_credit` and waits for its lease to
   lapse. The rollout drains before a switch.
3. **The worker's `/metrics` carries the build gauge only**; its counters (claimed, reaped,
   drains) are in `/readyz`'s body, and the alerts' `worker.prom` textfile is not written.
4. **Readiness checks the pool, the bucket and the cache once, at startup**; afterwards a
   dead dependency surfaces through W3's crash-only path (a runner dies → drain → exit 1 →
   `Restart=always`), not through `/readyz`.
5. `WORKER_HEALTH_PORT` is not settable through the installer (a `--set` is refused): `lib.sh`
   and `60-verify-local.sh` probe 8002. Moving it is a unit + script change.
6. **The rehearsal deploys dev mode**; a pilot install is rehearsed as a refusal only (no HTTPS
   identity source, no seeded catalog, 29 GiB free vs the 60 GiB budget), and the worker unit
   runs on the dev env file.
7. **P-21 on this host**: the e3b2 block's fixed ports (fake vLLM 56780/56782) were taken by
   other lanes' ephemeral client sockets several times (R9, R11a, R11b); each re-run passed.
   The gate's only red at `fb8babb` is one such case.
8. **d4 was used by another lane at the same time**: at 00:28Z the M lane
   (`codex-mpilot`, `tests/m/test_pilot_mutants.py -k pg_mutant`) ran with `INFRX_D_TASK=d4`
   (container `infrx-d4-postgres`, lock `/tmp/infrx-d4-postgres-55435.lock`: a different
   TMPDIR, so the harness's lock does not see it). My runs and theirs did not overlap in a way
   that failed; the transient R4b baseline red may be one.
9. rc08b and the tests/w drain prove the finished-in-bound path; the released-at-bound path
   of a PROCESS (a generation longer than the drain) is W3's in-process case and rc08 only.

## Integration requests

1. **Coordinator - the rollout checklist's W-steps for the worker unit.** ROLLOUT-PREP's
   request 2 is satisfied (the in-image probe passes with the entry point). At 50-install the
   unit needs no change; `INFRX_SET` already carries `WORKER_CONCURRENCY=8`. Add to
   `60-verify-local.sh` (or the checklist): `curl -fsS 127.0.0.1:8002/metrics | grep
   'infrx_build_info{process="worker",revision="<RELEASE>",image="<INFRX_IMAGE>"} 1'`; at any
   worker stop/restart: `journalctl -u infrx-worker | grep 'drained:'` reads `N finished, 0
   released`; a start refusal reads `infrx.worker: refusing to start: ...` with exit 2 (the
   unit restarts every 5 s until fixed). The worker unit is `PartOf=` the engine, so an
   engine restart drains it first (unchanged).
2. **Coordinator / M - a product preparation worker (Limit 1)** - the blocker for the pilot
   serving a job: claim `prepare_dispatch`, `media.prepare`, `prepared(lease, refs,
   prompt_tokens=<exact count>)`. Natural home: this process (a second `WorkerLoop` on the
   preparation kind, over the same stores), once the prompt count has a producer (W2 request
   3; vLLM's `/tokenize` on the pinned engine is one candidate).
3. **Phase-3 lane (E3B) / I3B** - merge this branch's `tests/integration` changes: the pilot
   box's worker IS `python -m infrx.worker` (reconciled with `40d9105`; nothing emulated for
   the worker any more), rc08b's body (I3B's drill: please review), rc00 without a pinned
   pending, `recoverykit.OWNERS = {}`, `i3bm57` retired / `i3bm117` added, `stack.py`'s
   comment. The gate at `fb8babb` has no PENDING left.
4. **D:** none.
5. **ROLLOUT-PREP lane:** `a670ec6` is in this branch byte-identical (`723a50c`); this branch
   also changes `rehearse.sh` steps 0-5 (above) - merge `codex/rollout-prep` after it and
   take both.
6. **Coordinator:** the d4 overlap (Limit 8) - two lanes hold `INFRX_D_TASK=d4`.

## Verification log

- 2026-09-24: Authored from the runs above (logs under the coordinator scratchpad's `i2br4/`
  and a few at its top level, sha256 prefixes quoted). Containers: `infrx-worker-minio`
  (127.0.0.1:55781, removed after the runs), the D harness's `infrx-d4-postgres` and
  `infrx-worker-valkey` (55435/55465, removed by the harness), the rehearsal's `infrx-wrk-*`
  (removed by its teardown), the e3b2 stack (`infrx-e3b2-*` and the journey PostgREST - the
  coordinator's reassignment; torn down by the gate). No AWS, hosted Supabase or pilot-box
  contact; nothing pushed.
