# E4C run 2 on the live release e6a8b40a — 2026-10-02 (window e4c-side-e6a8b40a-20261002T0005Z)

Certify run `20261002T020948Z` on the pilot box (RELEASE = the installed e6a8b40a; hosted 0001–0059; the window's log dir `~/infrx-e4c/20261002T000532Z` on the coordinator host; the resumed window log `~/infrx-e4c/run2b-20261002T0005Z/e4c-resume-h6.log`). Artifacts: `models/marlin2b/results/E4C-box-e6a8b40/run2-20261002T020948Z/` (report.json, certify.log, work/, and the read-only diagnosis logs taken from the box after the run: `cache-diag.log`, `db-diag.log`, `pg-diag2.log`–`pg-diag5.log`). Exit 1 at 06:31Z (14,842 s). The window stopped at `REPORT STOP` (05 §7: a rerun is a new qualifying run; criteria unchanged); `fetch` ran afterwards (`--only fetch`).

| Cell | Verdict | Finding | Classification (05 §7) |
|---|---|---|---|
| preconditions, config-pin, served-build | PASS | the box serves the report's tree e6a8b40a (git + release image); engine options digest pinned | — |
| e4b.a.sop-parity | PASS | 52.7 s; over-cap refused, parity held | — |
| e4b.a.dataset-resume | PASS | 24 items; first run interrupted (exit 130, 8 accepted), resume exit 0, reconciled; no double accept (run 1's hold-visibility oracle fix `6462ed06` held) | — |
| e4b.b.envelope (declared 0.5/s) | PASS | measured passing rate 0.5/s; at 0.5/s: 0/126 failures, 0 refused, ttft_p95_short 4.37 s, latency_p95 7.97 s, e2e_p95_per_clip_minute 14.06; the 1.0/s rung fails only `rejections` (2 within-cap refusals); the 2.0/s rung refuses 47 and latency_p95 18.1 s (the declared envelope is 0.5/s) | — (row 87's TTFT tail: p95 4.37 s at 0.5/s, p50 1.28 s) |
| e4b.b.soak (0.25/s × 14,400 s, corpus `full`) | **FAIL** | `failure_rate` 443/3374 platform-caused (436 × 503 `dependency_unavailable`, 7 `deadline_exceeded`; plus 5 `stream_interrupted` among the 448 failed attempts); `duration_cap` lists c012/c038/c051 as "over cap not refused" — each is a 112 s clip whose attempt got the 503 instead of the 400 (the dependency failed before the cap refusal; every other attempt on those clips was refused 400 `unsupported_media`); host growth +108.8 MiB pass; latency drift p50 2.94 → 3.81 s pass; client exit 0; VALID (driver lag max 0.33 s) | **platform defect, database layer** — see below |
| e4b.b.overload (burst 32) | FAIL | bench cell not VALID: driver lag 1.073 s > the 1.0 s bound (the client, which runs on the box's 8 vCPUs beside the gateway, worker and engine, limited the offered load; run 1 saw 1.020 s) | invalid measurement — rerun the cell (the client host is the recurring cause: two runs, same reason) |
| release-identity | PASS | one clean tree e6a8b40a | — |

Not run (the stop at `report`): h6-close, e1b, wc6a/wc6b, wc7, the tenant-2 key and journeys, drills, wc9, drift-end, cleanup. The cleanup prompt still needs the operator's reviewed yes.

## The soak's 503s: database pool exhaustion on the hosted PostgreSQL (classification)

Timeline (soak attempts by 10-minute bucket, `work/soak-raw.jsonl`): no 503 for the first 80 minutes, then episodes that grow — 28 (03:31–03:41Z), 65 (04:21–04:31Z), 18 (04:31–04:51Z), 101 + 3 × 429 (05:01–05:21Z), 224 (05:31–06:01Z) — and 0 in the last bucket. Retry-After 30 on 348 of them, 5 on 88.

What the box's journal says for the run window (`db-diag.log`, 02:09–06:20Z, gateway + worker units, masked):

| Line | Count | Source in the tree |
|---|---|---|
| `psycopg_pool.PoolTimeout: couldn't get a connection after 5.00 sec` | 176 | `database_pool_connect_timeout_s` = 5.0; the box's `DATABASE_POOL_MAX_SIZE=6` (gateway and worker each) |
| `readiness probe failed or did not answer in 10.0s` | 153 | `gateway/pilot.py` `Probe` (`PROBE_EVERY_S` 5.0, `PROBE_TIMEOUT_S` 10.0): the journal probe is `infrx.journal_usage()` |
| `a durable dependency did not answer within 20.0s` | 31 | `routes/intake.py` `DEPENDENCY_BOUND_S` → 503 Retry-After 5 |
| `the lifecycle store is unreachable` (Retry-After 30) | — (the 503 body) | `state/lifecycle.py` `_call` (a psycopg `OperationalError`, which the pool timeout is) |
| worker tracebacks ending in `expire_journal` (`state/rpc.py` → psycopg execute) | 8 groups, 04:35–06:05Z | the worker's `journal_expire` loop (`worker/__main__.py` `every`) |
| retention sweep `aborted: dependency_unavailable` | 1 at 05:59:58Z | `media/retention.py` |

Per-10-minute count of journal lines mentioning the outage: 2 (02:xx), 14 (03:xx), 42 (04:xx), 99 (05:xx), 28 (06:00–06:20). Valkey: 0 errors; the Lab control service: 0.

Ruled out: the media processing cache (138 MB of 50 GiB, 0 held locks after the run, `cache-diag.log`), disk (381 GB free), the engine (0 engine-side errors; the 2.0/s envelope rung shows capacity refusals as 429, not 503).

What the hosted database shows (read-only, after the run, `pg-diag2.log`/`pg-diag3.log`; PostgreSQL 17.6, `max_connections` 60, 287 MB, pooler): `infrx.jobs` 12,961 live rows / 49 MB with **197,382 sequential scans reading 1.06 G tuples (≈5,400 tuples per scan)** against 25 M index scans; `stream_chunks` 92,608 rows / 126 MB with 13,317 sequential scans (31 k tuples each); `idempotency` 12,827 sequential scans. `jobs_queue_deadline_idx` has **0** scans and `jobs_org_created_idx` 68, while `jobs_settlement_target_key` has 24 M. The journal probe's parts plan on indexes and take 17 + 26 ms idle (`journal_usage()` 39 ms idle); `expire_journal`'s candidate query plans on `stream_chunks_expiry_idx`. `pg_stat_statements` is not readable by the app role (schema `extensions`), so the statement that sequentially scans `jobs` is not yet named from the database side.

Classification (corrected 07:20Z, `pg-diag4.log`, `pg-diag5.log`): a **platform defect in the database layer, most likely the hosted compute tier under sustained load**. The sequential-scan counters run since the statistics epoch 2026-08-25, so 197k scans of `jobs` is about one every 17 s (the 5-second readiness probe's `journal_bytes_charged()` OR-join, `0011_admission.sql:128`, is the only per-tick statement that cannot use an index; the operator-run grep of the SQL functions and the EXPLAINs show the admission counts, the deadline sweep, the reconcile sweep and the readiness-waiting query all plan on indexes, 16–27 ms idle; only the content-scrub sweep `0020_content_lifecycle.sql:417` plans as a full scan, 134 ms idle). The instance: PostgreSQL 17.6 behind the Supabase pooler, `shared_buffers` 224 MB, `effective_cache_size` 384 MB, `work_mem` 2 MB (9,111 temp files since the epoch), `max_connections` 60, `statement_timeout` 15 s, buffer hit 100% — the smallest compute class, whose CPU is burstable. Under the soak (0.25/s accepted plus the journal appends, the outbox pump, settlement and the 5-second probe) the instance slows in growing episodes over hours and recovers when the load stops; the gateway's and worker's 6-connection pools then exhaust at the 5 s connect timeout, the readiness probe misses its 10 s bound, and admission answers 503 `dependency_unavailable`. Not yet proven from the database side: `pg_stat_statements` and `pg_stat_user_functions` are not readable by the app role (`track_functions` is `none`), and no activity sample was taken during the run. What proves it: the Supabase project's compute size and its CPU / CPU-credit / IO graphs for 02:10–06:20Z, and the Query Performance page for the window (operator reads); for the rerun, a read-only `pg_stat_activity` sampler on the box every 10 s during certify. Fix candidates, in order: confirm and size the hosted compute (operator; an instance change, not a migration); make the readiness probe cheap (the Python probe asks `journal_usage()` — four aggregates — every 5 s; a bounded probe, or a longer `PROBE_EVERY_S`, is a code change with no window); a partial index for the scrub sweep (LOCAL-ONLY migration until an R151 window); `DATABASE_POOL_MAX_SIZE` only with the pooler's limits in view. None of these is applied; the rerun after whichever lands is a new qualifying run.

## Decisions this run supports

- BACKEND-READY: **not met** (05 §7: a FAIL cell). APP-PILOT follows BACKEND-READY.
- The envelope at 0.5/s passes for the second time (run 1 failed only `e2e_p95_per_clip_minute`), so the declared capacity is not the blocker; the soak is.
- P-17 (public models enumeration) is not decided by this run.

## Verification log

- 2026-10-02T07:20Z: classification corrected after the statistics epoch (2026-08-25) and the EXPLAINs: the sequential scans are not a hot path; the hosted compute class is the leading cause; `pg-diag4.log`/`pg-diag5.log` added.
- 2026-10-02T06:52Z: written by the coordinator from report.json, the window logs and four read-only box diagnosis steps (`infra/rollout/ssm.sh` with scratchpad scripts, command ids in the logs); secrets scan of the copied files clean; no box, hosted-database or AWS state changed.
- 2026-10-02T09:08Z: operator decision — hosted compute to Medium now, Large after the v1 launch is tested in production; the dashboard reads (CPU/credit graphs, Query Performance) remain requested for the record.
- 2026-10-02T12:31Z: code half landed on the tree (merge #101, api-probe): the journal readiness probe is one bounded primary-key statement (R273); 0068 (merge #101, LOCAL-ONLY) carries the scrub-sweep index and the `journal_bytes_charged()` rewrite; the `pg_stat_activity` sampler step was denied by the classifier (register row 102). The next qualifying run waits for the Medium instance to be in place, the sampler, and a consumer window that installs a release carrying the probe.

