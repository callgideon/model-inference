# I8-M6-WIRING — M6 phase 2 wiring request 4 (retention/cache panels, alerts, bucket rule)

- Base: `codex/m6-phase2` @ `8fe53ed9` (M6 phase 2 on D10/M5/M6p1). Anchored on the names at that tip; M6's verification fix round may still rename (the vocabulary test reads `retention.py` and fails on a rename of a retained/aborted reason).
- Head: `26e05761` on `codex/i8-m6-wiring` (+ this evidence commit). Not pushed.

## Finding that shaped the slice

At `8fe53ed9` M6 records **no** metric: `RetentionCollector` only fills a `Report` and logs one line; `ProcessingCache` keeps no counters. `infrx/observe/metrics.py` FAMILIES declares no `retention_*` / `processing_cache_*` family. OB-10 requires every dashboard `rows` panel to name a declared family, so the panels cannot live in `rows` yet. Delivered instead:

- `infra/alerts/dashboard.json` gains a `pending` section (ignored by OB-10): the proposed family declarations (kind, closed label vocabularies) and two rows of panels. `tests/i` fails once a pending family is declared in FAMILIES, forcing the move into `rows`.
- Rules live in `infra/alerts/operations.json` (version 2, merged `a1+o2`), not `alerts.json`, so OB-11's FAULTS table (I3B-owned) is untouched. Their families are in `pending_producers` (the existing I8 convention).
- `infrx_processing_cache_bytes` is already produced by `infra/observe/host-probe.sh` (process=host); its panel is in `pending` because it is not in FAMILIES either.

## Family -> panel

| family | kind | labels (closed) | panel | rule |
|---|---|---|---|---|
| infrx_retention_passes_total | counter | - | Retention passes | - |
| infrx_retention_aborted_total | counter | reason: dependency_unavailable, object_store_unavailable | Aborted passes by reason | - |
| infrx_retention_consecutive_aborted_passes | gauge | - | Consecutive aborted passes | RetentionAborting (>= 3, exact: M6) |
| infrx_retention_last_success_timestamp_seconds | gauge | - | Last completed pass | RetentionStale (age > 1200 s, P-25) |
| infrx_retention_deleted_total | counter | location: object_store, database | Deleted by location | - |
| infrx_retention_retained_total | counter | reason: claim_held, claim_lost, foreign_key, lease_short, not_claimable, not_eligible, not_found, not_ready, reference_live, stale_lease | Retained by reason | - |
| infrx_retention_delete_failed_total | counter | - | Delete failures | RetentionDeleteFailures (increase > 0, P-25) |
| infrx_retention_ack_lost_total | counter | - | Lost acknowledgements | - |
| infrx_retention_pending_delete_seconds | gauge | - | Oldest pending delete | RetentionPendingDeleteOld (> 900 s = 300 + 2x300, P-25) |
| infrx_processing_cache_bytes | gauge | - | Cache bytes | ProcessingCacheLarge (existing, 60 GiB) |
| infrx_processing_cache_evicted_total | counter | reason: high_water, expired | Evictions by reason | - |
| infrx_processing_cache_refused_total | counter | - | Refused puts | ProcessingCacheRefusing (increase > 0, P-25) |

Runbook sections (`infra/runbooks/observe.md`): `#retention-stalled`, `#retention-delete-failures`, `#processing-cache-high-water`, `#bucket-lifecycle-rule`.

## Bucket lifecycle rule

`apps/infrx-api/deploy/s3-lifecycle.json`: one rule, `AbortIncompleteMultipartUpload` after 1 day, `Filter.Prefix` = `infrx/` (= `DeploymentSettings.s3_media_prefix`), no Expiration/Transition. Runbook stanza names `$S3_MEDIA_BUCKET` only and **merges** (get, jq, put, get): the bucket is shared and a put replaces the whole configuration. Not applied (coordinator op).

## Also carried

`codex/i8-panels`' db_pool row (465b8c20) applied verbatim to `dashboard.json`: at `8fe53ed9` OB-10 fails without it (7 `infrx_db_pool_*` families unpaneled; pre-existing, not caused here). Identical hunk, so it merges cleanly with `claude/consumer-v1`.

## Commands (worktree root unless noted)

| command | exit | result |
|---|---|---|
| `make api-env` | 0 | env built |
| `cd apps/infrx-api && INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_observe.py` (before implementation) | 1 | 3 failed, 13 passed, 1 xfailed (red: the three new cases) |
| same, after | 0 | 16 passed, 1 xfailed |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/recovery/test_observe.py` (before db_pool row) | 1 | 1 failed (ob10: db_pool families, pre-existing), 15 passed |
| same, after | 0 | 16 passed |
| `apps/infrx-api/.venv/bin/python tests/integration/backend/recovery/mutants_i3b.py --only i3bm24` | 0 | 1/1 killed |
| `cd apps/infrx-api && INFRX_D_TASK=i8 uv run --frozen python tests/i/mutants.py m6_panel_missing m6_abort_rule_blunted bucket_rule_whole_bucket` | 0 | 3/3 killed (first run: m6_panel_missing broken_runner - left invalid JSON; anchor fixed) |
| `cd apps/infrx-api && INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_mutants.py` | 0 | 50 passed |

No docker used by these cases; no AWS, hosted DB or box.

## Wiring requests

**WR-I8-M6-1 (M6 + observe owner; I8 follow-up in the same merge).** Record what the collector and cache already know:
1. `infrx/observe/metrics.py` FAMILIES: add the 11 families above that are not host-probe-only (plus `infrx_processing_cache_bytes` if the worker should also export it), exactly as `dashboard.json` `pending.families` declares them (`Spec(kind, help, ((label, frozenset(values)),))`).
2. `infrx/media/retention.py` `RetentionCollector.run(interval_s, *, sleep, metrics=None)`: after each `sweep()`, `metrics.inc("infrx_retention_passes_total")`; `inc(..._aborted_total, reason=report.aborted)` and `set(..._consecutive_aborted_passes, n+1)` when aborted, else `set(..., 0)` and `set(..._last_success_timestamp_seconds, time.time())`; `inc(..._deleted_total, location=loc)` per `report.deleted`; `inc(..._retained_total, n, reason=r)` per `report.retained`; `inc` delete_failed / ack_lost by their counts; `set(..._pending_delete_seconds, report.max_pending_delete_s)`.
3. `infrx/media/prepare.py` `ProcessingCache`: an optional `metrics` registry; `inc(..._evicted_total, reason="high_water")` in `_make_room`, `reason="expired"` in `sweep`, `inc(..._refused_total)` before the `DependencyUnavailable` raise.
4. Worker compose (M6 wiring 1): pass the worker's `Registry` to both.
5. I8 follow-up, same merge: move `pending.rows` into `rows`, drop `pending`, drop the landed names from `operations.json` `pending_producers` (tests/i fails until done - that is the oracle).
Proof: `tests/integration/backend/recovery/test_observe.py` ob01/ob10 over the new families, and `tests/i/test_observe.py` producer + retention cases.

## Open

- P-25: retention interval (thresholds assume 300 s), cache high water `PROCESSING_CACHE_MAX_BYTES`, alert destination.
- Bucket rule not applied; needs the coordinator's AWS session.
- M6's verification fix round may rename reasons; rerun `tests/i/test_observe.py` on the merged tip.

## Estimate (remaining, I8 side)

Optimistic 0.5 h / likely 1 h / pessimistic 2 h, confidence medium: the WR-I8-M6-1 follow-up (move panels, trim pending) once M6 records; applying the bucket rule is a coordinator step.
