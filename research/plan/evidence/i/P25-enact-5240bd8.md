# P25-ENACT — P-25 enacted as configuration (I8/M6)

Base `74183655` (codex/wave4b-union). Code head `5240bd8c` on `codex/p25-enact`. Local only:
no box, AWS, SSM, hosted DB or secret. Decision: research/plan/15-pending-inputs.md,
"Decisions 2026-09-25", the P-25 row.

## Per-item diff

| # | Item | Change |
|---|---|---|
| 1 | Retention grace | `config.DeploymentSettings.retention_grace_s: float = 3600.0` (config.py:375, env `RETENTION_GRACE_S`). **Seam:** `infrx/worker/__main__.py:141-142`, `lifecycle = PgLifecycle(connect, limits=limits, grace_s=deployment.retention_grace_s)`. This is the one lifecycle the `RetentionCollector` and `MediaPreparation(content=)` share, so `content_register` stamps `eligible_at = now + 3600 s`. The library default `lifecycle.GRACE_S` (604,800 s, lifecycle.py:38) is unchanged. The preparation runner's second `PgLifecycle` (`readiness=`, __main__.py:166) only calls `readiness`/`claim_preparation`, which never read `grace_s`, so it is left as is. The gateway's upload lifecycle is WR-P25-1. |
| 2 | Cache cap | `processing_cache_max_bytes` changed from 64_424_509_440 (60 GiB) to 53_687_091_200 (50 GiB) (config.py:371). The low water is `infrx/media/prepare.py:117 LOW_WATER = 0.8` and is unchanged (a test asserts it). The config.py comment, infra/README.md §2 and the disk-budget row, plus a preflight comment (preflight.py:83-84), now say that the 60 GiB is R's disk budget, that it is unchanged, and that the 50 GiB cap sits under it. `DISK_BUDGET` is unchanged. |
| 3 | Intervals | `retention_interval_s` and `cache_sweep_interval_s` stay 300.0. Both were already pinned in `DEPLOYMENT_EXPECTED`. Added `RETENTION_GRACE_S: 3600.0`, and `PROCESSING_CACHE_MAX_BYTES` is now 53687091200. `infra/alerts/operations.json` `ProcessingCacheLarge`: threshold 53687091200, summary "50 GiB high water", and `threshold_status` `derived: P-25 (decided 2026-09-25), ...` replaces `⚠️ TO BE VERIFIED (P-18)`. It must start with `derived`/`exact` because `test_observe` requires that. No other alert changed. |
| 4 | Runbooks | `infra/runbooks/restore.md` has a new "Dump cadence while PITR is off" section: a verified dump before every migration or rollout and daily during E4C, keeping the 7 newest. Verified means A6 `pgrestore.py check` equal. The dump stays operator-run per A1-A6, and PITR is a separate paid decision. `infra/runbooks/rollout.md` §3 gets a new "Known-good record": known-good.py's six checks exit 0 with `--bundles`, plus `85-known-good-box.sh`, with the 0023 schema_proof (KNOWN-GOOD-PROOF-aab4b41.md). It also says to keep `RETENTION_GRACE_S` out of `INFRX_SET`, because known-good.py's `config` check would otherwise refuse a candidate older than this lane. Verification-log entries were appended to all three docs. |
| 5 | Not touched | The SNS/webhook destination, the box and the TTL constants (result 86,400, journal 3,600, cache/source retention 604,800, idempotency 86,400, claim 300) are unchanged. |

**Scope note:** `apps/infrx-api/deploy/preflight.py` `TUNABLE` gains `"RETENTION_GRACE_S"`. That is one name, outside "comments/DISK_BUDGET notes only". Without it `tests/i/test_packaging.py::test_backend_deploy__the_config_schema_is_every_name_the_runtime_reads` fails, because every name `from_env` reads must be in the schema. M6-WIRING added its four names to the same place. Revert the line and the field together if the coordinator prefers to route it.

`tests/contracts/test_config_and_imports.py` `DEPLOYMENT_EXPECTED` was edited on the pins the brief names: the value of `PROCESSING_CACHE_MAX_BYTES` and the new `RETENTION_GRACE_S` entry.

## Invariants

- Retention can only get shorter. The grace is the wait between content's last reference and its collection eligibility, and it drops from 7 days to 1 hour. No TTL moved.
- CREDIT and USD are untouched.
- Each setting has one source of truth (`DeploymentSettings`) and a `DEPLOYMENT_EXPECTED` pin.
- The alert, runbook and comment figures name P-25.
- On rollback to a release older than this lane, the worker's grace goes back to 604,800 s, which was the previous behaviour.

## Fails-before (tests first, then the change)

| Command (apps/infrx-api) | Result before | After |
|---|---|---|
| `uv run --frozen --no-sync pytest -q tests/w/test_worker_main.py -k "grace or p25s or RETENTION_GRACE"` | 3 failed: `the_lifecycle_grace_is_the_deployments` (`604800.0 == 3600.0`), `the_cache_high_water_and_its_alert_are_p25s` (`64424509440 == 50*2**30`), `each_missing_setting_refuses_startup_by_name[RETENTION_GRACE_S]` (DID NOT RAISE) | 3 passed |
| `uv run --frozen --no-sync pytest -q tests/contracts/test_config_and_imports.py` (pins updated first) | 7 failed / 269 passed (`test_every_deployment_name_and_default_is_frozen` + RETENTION_GRACE_S parametrized cases) | exit 0 (in the 312 below) |

## Checks (code head 5240bd8c)

| Command | Exit | Counts |
|---|---|---|
| `INFRX_D_TASK=m6 uv run --frozen --no-sync pytest -q tests/w/test_prep_worker.py tests/w/test_worker_main.py tests/m/test_retention.py` | 0 | 161 passed, 7 skipped |
| `uv run --frozen --no-sync pytest -q tests/contracts/test_config_and_imports.py tests/i/test_packaging.py tests/i/test_envcheck.py` | 0 | 312 passed |
| `uv run --frozen --no-sync pytest -q tests/i/test_rollout.py` | 0 | 11 passed |
| `uv run --frozen --no-sync pytest -q tests/i/test_observe.py` | 1 | 17 passed, 1 xfailed, **1 failed, pre-existing** (see below) |
| `uv run --frozen --no-sync pytest -q tests/w/test_worker_main_mutants.py` | 0 | 7 passed, 1 skipped (list well-formed, every case covered, anchors present) |
| `INFRX_D_TASK=m6 uv run --frozen --no-sync python -m tests.w.worker_main_mutants` (the in-memory list) | 0 | **40/40 killed** (35 existing + 5 new) |
| `python3 research/plan/scripts/validate_plan.py` (repo root) | 0 | 3 PASS lines; 933 links / 227 docs |

The 7 skips are the `_pg__` and MinIO cases (`test_worker_main.py:699,854,904,967`, `test_prep_worker.py:888,922,961`). They need `INFRX_M_S3_ENDPOINT` and a Valkey port (`INFRX_D2_VALKEY_PORT`), and the m6 block (postgres 55444, s3 55471) has no Valkey, so they were not run. No container was started. For the coordinator's union gate: the grace reaches `content_register` as a float, which the D10 functions already take (M tests run them with 60 s).

**Pre-existing failure:** `test_observe.py::test_ops_continuous__the_alert_rules_without_a_producer_are_exactly_the_known_ones` fails at base. I reproduced it with `operations.json` reverted to base. `KNOWN_UNPRODUCED` still lists `UnsettleableJobs`, which now has a producer. That alert rule is in `alerts.json`, which this lane did not touch. See WR-P25-4.

## New mutants (tests/w/worker_main_mutants.py), all killed

| Mutant | Killed by |
|---|---|
| `main_retention_grace_dropped` (lifecycle composed without `grace_s`) | `the_lifecycle_grace_is_the_deployments` |
| `main_retention_grace_fixed` (`grace_s=3600.0` literal, not the setting) | same |
| `config_retention_grace_a_week` (default back to 604,800) | same |
| `config_cache_high_water_60gib` (default back to 60 GiB) | `the_cache_high_water_and_its_alert_are_p25s` |
| `alert_cache_threshold_drifts` (operations.json threshold back to 60 GiB) | same |

The mutation layout also copies `deploy/preflight.py` and `infra/alerts/operations.json`, because the P-25 case reads both.

## Wiring requests (not applied)

- **WR-P25-1 (gateway composition, `apps/infrx-api/infrx/gateway/pilot.py:237-240`):** the gateway's upload lifecycle still uses the 604,800 s library grace for `upload_complete`, which sets a completed upload source's eligibility. P-25's 3,600 s should apply there too. Patch: `def _pg_lifecycle(connect, settings)` returns `PgLifecycle(connect, limits=settings.pilot, grace_s=settings.deployment.retention_grace_s)`, and the caller at pilot.py:222 passes `settings`. Proof test: the pilot app's `lifecycle` adapter has `grace_s == 3600.0`, and 11.0 with `RETENTION_GRACE_S=11`. A mutant drops the argument.
- **WR-P25-2 (`infra/runbooks/observe.md:189`):** replace "`PROCESSING_CACHE_MAX_BYTES`, ⚠️ TO BE VERIFIED (P-25)" with "`PROCESSING_CACHE_MAX_BYTES`, 50 GiB, P-25 decided 2026-09-25".
- **WR-P25-3 (`infra/alerts/operations.json`, other rules, left unchanged per the brief):** in `RetentionPendingDeleteOld` (900) and `RetentionStale` (1200), the "300 s assumed ... ⚠️ TO BE VERIFIED (P-25): the approved interval" is now decided. Use `derived: ... retention interval 300 s (P-25, decided 2026-09-25)`. In `ProcessingCacheRefusing`, replace "⚠️ TO BE VERIFIED (P-25): the high water (PROCESSING_CACHE_MAX_BYTES) itself" with "the 50 GiB high water (P-25)". Thresholds stay the same.
- **WR-P25-4 (`apps/infrx-api/tests/i/test_observe.py` `KNOWN_UNPRODUCED`):** remove `UnsettleableJobs`, which is now produced. This failure predates this lane.
- **WR-P25-5 (P-25 row, `research/plan/15-pending-inputs.md`, coordinator):** record the enactment at 5240bd8c. The row says `bda15866` needs a schema_proof through 0025. The tree ends at 0023 and the proof reaches 0023, so extend it when 0024/0025 land.

## Operator inputs (coordinator-held)

1. Box NVMe free space: `df -B1 /opt/dlami/nvme` (on the box, through the coordinator). The P-25 row keeps 50 GiB ⚠️ TO BE VERIFIED until this read shows it fits. Preflight still refuses below 60 GiB free for R.
2. The SNS topic `infrx-pilot-alerts` and its e-mail subscription (or the webhook SSM parameter), then `72-observe-install.sh` and `74-alert-test.sh`. This lane did not touch them.
3. The PITR read: `supabase_policy.py` with `SUPABASE_ACCESS_TOKEN` (restore.md "Backup and PITR policy"). Until it shows PITR on, the new dump cadence applies.

## Remaining effort

Optimistic 0.25 h, likely 0.75 h, pessimistic 1.5 h; confidence medium. What is left: the coordinator's review/merge, WR-P25-1 (gateway grace), the doc/alert wirings, and the union gate's `_pg__` run.

## Fix round (code head 1c62eef8; handback head 14de75e6 reviewed)

Findings 0-P25R-1, 1-P25R-1, 1-P25R-2 and 1-P25R-3 are all fixed in owned paths. Nothing was pushed. No box, AWS, SSM, hosted DB, secret or container was used.

**Correction to the headline and to WR-P25-1 (0-P25R-1, 1-P25R-1).** P-25's 3,600 s grace applies **only to content the worker registers**. The gateway has its own `PgLifecycle(connect, limits=limits)` (`infrx/gateway/pilot.py:237-240`, built at :222). It passes that lifecycle as both `uploads=` and `content=` to `MediaUploads` (:312), so these still stamp the library's 604,800 s:

- `upload_complete` (lifecycle.py:160);
- every source and payload that `MediaUploads._register` writes (`infrx/media/store.py:231-242`, called for `ContentKind.source` at :301, which covers URL-fetched and uploaded sources, and for `ContentKind.payload` at :352).

On a re-register, 0022 (`0022_preparation_refusal_and_flag_writer.sql:266`) sets `eligible_at = greatest(eligible_at, now + grace)`. A later 3,600 s registration by the worker therefore cannot shorten a gateway-stamped 7-day eligibility.

This does not extend retention. It is the status quo from before this lane. The coordinator should record P-25's grace as enacted for worker-registered content only, and not for gateway-registered sources or payloads, until WR-P25-1 merges.

**WR-P25-1 (reworded, supersedes the text above):** the gateway's lifecycle, `pilot.py:237-240`, stamps 604,800 s for `upload_complete` and for every source and payload that `MediaUploads._register` registers. The patch is the same one adapter change, which covers both paths:

- `def _pg_lifecycle(connect, settings)` returns `PgLifecycle(connect, limits=settings.pilot, grace_s=settings.deployment.retention_grace_s)`;
- the caller at pilot.py:222 passes `settings`.

The proof test is already committed as a strict xfail: `tests/w/test_worker_main.py::test_worker_main__the_gateways_content_grace_is_the_deployments` checks `adapters_from_env(...)["lifecycle"].grace_s == RETENTION_GRACE_S` (11 in the case). The patch must do three things:

1. Remove the `xfail` mark. Otherwise the test XPASSes, and strict mode fails it.
2. Replace the mutant `gateway_grace_wired_unrecorded` with one that drops `grace_s=` from `_pg_lifecycle`.
3. Add the PostgreSQL proof on the union: register a source through the pilot app and assert `eligible_at - registered_at == 3600 s`.

**1-P25R-2.** `infra/runbooks/restore.md` "Dump cadence while PITR is off" now says that P-25 is A9's "longer period" coordinator decision:

- While PITR is off, A9's removal after A8 does not apply.
- A9's container removal and `unset PGPASSWORD` still run.
- A dump whose A6 check is not equal is removed at once with A9's `rm`.
- After each new dump's A6 check is logged, the directory is pruned to the 7 newest with `ls -1d "$HOME"/infrx-backups/hosted-* | head -n -7 | while read -r old; do rm -rf -- "$old"; done`.

A9 itself is not edited, because it already allows a logged longer period.

**1-P25R-3.** The `infra/runbooks/rollout.md` "Known-good record" command now passes `--set` for each of §1's `INFRX_SET` names (S3_MEDIA_BUCKET, MAX_VIDEO_SECONDS, WORKER_CONCURRENCY, LARGE_BODY_LIMIT, DATABASE_POOL_MAX_SIZE) plus ENGINE_MAX_NUM_SEQS, which 50-install adds. That is rollback.md:62's list plus ENGINE_MAX_NUM_SEQS. The command also passes `--bundles s3://llm-bootcamp-641134885443/releases/`. The text says that `config` compares only the `--set` names.

Local run, `--applied 0023`, without `--bundles` (no AWS):

| Release | Six names | `--set RETENTION_GRACE_S` | No `--set` |
|---|---|---|---|
| 4226315 | `config` True, exit 0 | `config` False | `config` True (the vacuous pass) |
| bda1586 | `config` True, exit 0 | `config` False | `config` True (the vacuous pass) |

### Fails-before

| Case | Before (pre-fix runbooks / tree) | After |
|---|---|---|
| `tests/w/test_p25_runbooks.py::test_worker_main__the_known_good_record_passes_every_install_name` | failed: no command block in the section (`ValueError: not enough values to unpack`); the first draft, which scanned the whole section, failed on the missing --set names | passed |
| `tests/w/test_p25_runbooks.py::test_worker_main__the_dump_cadence_keeps_the_7_newest` (runs the prune under a temp `HOME` over 10 `hosted-<UTC>` dirs, twice) | failed: the section named neither A9's longer period nor a prune | passed |
| `tests/w/test_worker_main.py::test_worker_main__the_gateways_content_grace_is_the_deployments` (`--runxfail`) | `assert 604800.0 == 11.0`, which is the gap | xfailed (strict); the mutant `gateway_grace_wired_unrecorded`, which is WR-P25-1's effect, is killed |

The two runbook cases are in their own file. The shared runner `tests/contracts/mutants.py:2835` compiles every mutated file as Python, so Markdown anchors come out `broken_runner` (seen on the first try: 41/43, both runbook mutants broken_runner). `test_worker_main.py` requires a mutant for every case. The mutations were therefore run by hand against the runbooks, and each was restored afterwards:

| Mutation | Result |
|---|---|
| prune `head -n -7` changed to `tail -n +8` (keeps the oldest) | 1 failed |
| `head -n -100` (removes nothing) | 1 failed |
| the record's `--set` lines deleted | 1 failed |
| `--set ENGINE_MAX_NUM_SEQS` dropped | 1 failed |
| pristine | 2 passed |

### Checks (code head 1c62eef8)

| Command (apps/infrx-api) | Exit | Counts |
|---|---|---|
| `INFRX_D_TASK=m6 uv run --frozen --no-sync pytest -q tests/w/test_prep_worker.py tests/w/test_worker_main.py tests/m/test_retention.py tests/w/test_p25_runbooks.py` | 0 | 163 passed, 7 skipped, 1 xfailed |
| `uv run --frozen --no-sync pytest -q tests/contracts/test_config_and_imports.py tests/i/test_packaging.py tests/i/test_envcheck.py` | 0 | 312 passed |
| `uv run --frozen --no-sync pytest -q tests/i/test_rollout.py` | 0 | 11 passed |
| `uv run --frozen --no-sync pytest -q tests/i/test_observe.py` | 1 | 17 passed, 1 xfailed, 1 failed (the same pre-existing `KNOWN_UNPRODUCED` case, WR-P25-4) |
| `uv run --frozen --no-sync pytest -q tests/w/test_worker_main_mutants.py` | 0 | 7 passed, 1 skipped |
| `INFRX_D_TASK=m6 uv run --frozen --no-sync python -m tests.w.worker_main_mutants` | 0 | **41/41 killed** (40 + `gateway_grace_wired_unrecorded`) |
| `python3 research/plan/scripts/validate_plan.py` (repo root) | 0 | 4 PASS lines; 933 links / 228 docs |

The 7 skips are the same `_pg__`/MinIO cases as before.

### Remaining effort

Optimistic 0.25 h, likely 0.5 h, pessimistic 1.25 h; confidence medium. What remains:

- the coordinator's review and merge;
- WR-P25-1, the gateway grace. Its proof is already committed as a strict xfail;
- WR-P25-2 through WR-P25-5;
- the union gate's `_pg__` run.
