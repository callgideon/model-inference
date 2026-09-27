# T2I evidence: inference analytics and content projection (implementation c9ed0e4)

Lane `trace-ship` (LW1), branch `codex/w5-trace-ship`, worktree `.claude/worktrees/codex-w5-trace-ship`.
Base `9a6c3685` (LW0 merged). Implementation head `c9ed0e4b`. Tasklocal key `t2i`: ClickHouse 57540 (native 57541), S3 57542.
Oracles: TRACE-RECOVER, TRACE-TENANT. Integration dependency D5: developed against a fake pins lookup (see WR-3); **not claimed integrated**.

## Changed paths (all owned)

- `apps/infrx-api/infrx/traces/ship/__init__.py`, `shipper.py`, `schema.sql`
- `apps/infrx-api/tests/t/ship/test_ship.py`, `mutants.py`, `test_mutants.py`

`spool.py` is untouched; the shipper uses only its public interface (`segments`, `read_segment`, `ack`).

## What it does

- `Shipper(spool, projection, objects, pins=None).ship()`: one pass over the spool's **sealed** segments. Each record's
  content is written write-once (`put_if_absent`) at `trace/{org_id}/{segment}:{position}` (derived here; the
  envelope's `content_ref` is never used as a key), then one row per record is inserted, then the segment is acked.
  Held (retried next pass, never acked) on: projection outage, pins-lookup outage, a content object that did not
  land (the row still ships with `content_stored=0`), an unreadable segment, a poison frame, a failed ack.
- `schema.sql`: `trace_envelopes`, `ReplacingMergeTree(content_stored)` ordered by `(org_id, trace_id)`,
  `trace_id` = the spool's stable `(segment, position)` identity. Reads are `FINAL` with the org bound as a parameter.
- Rows carry `serving_version_id`, `rate_card_version`, `policy_version` (D5's `AdmissionPins`, looked up by the
  envelope's own `(org_id, request_id)`), `model_revision`, `price_version`, `loss_reason`, `content_complete`.
- `read_content(projection, objects, org_id, request_id)`: org-bound row lookup; follows only a stored key under
  `trace/{org_id}/`.
- `shipping_enabled(limits)`: False unless `TRACE_SPOOL_DIR`, `CLICKHOUSE_URL` and `S3_TRACE_BUCKET` are all set
  (defaults OFF). Nothing composes a shipper yet (WR-3).

## Commands (from `apps/infrx-api` unless stated)

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `uv run --frozen pytest -q tests/t/ship` (tests written first, before `infrx/traces/ship` existed) | 2 | RED: `ImportError: cannot import name 'ship' from 'infrx.traces'`, 1 collection error |
| 2 | `uv run --frozen pytest -q tests/t/ship/test_ship.py` | 0 | 11 passed, 9 skipped (stack half skips visibly without `INFRX_T2I_STACK=1`) |
| 3 | `INFRX_T2I_STACK=1 uv run --frozen pytest -q tests/t/ship/test_ship.py` | 0 | 20 passed (memory + real ClickHouse 25.8.33.6 / MinIO on 57540/57542) |
| 4 | `INFRX_T2I_STACK=1 INFRX_MUTANTS=all uv run --frozen pytest -q tests/t/ship/test_mutants.py` | 0 | 24 passed: 19/19 mutants killed, well-formed, every-case-covered, 3 runner self-tests |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q -rs tests/t/ship/test_mutants.py` | 0 | 22 passed, 2 skipped (the two ClickHouse-only mutants, skip names owner) |
| 6 | `uv run --frozen pytest -q tests/t/ship/test_mutants.py` (default subset) | 0 | 11 passed |
| 7 | `uv run --frozen pytest -q tests/t/test_trace_spool.py tests/t/test_trace_mutants.py tests/contracts/test_config_and_imports.py` | 0 | 349 passed |
| 8 | `make api-test` (repo root) | 2 | 3795 passed, 810 failed, 63 skipped, 1 xfailed. **All 810** are `tests.d.pgharness.HarnessBusy`: port 55432 lock held by the concurrent `codex-w5-lab-access` lane (809 raised it directly; 1 is `test_lifecycle_conformance` whose PG recording hit it) |
| 9 | `uv run --frozen pytest -q --lf` once the 55432 lock was free | 0 | 802 passed, 8 xfailed: every #8 failure passes |

Stack (task-local, removed after the run): `infrx-t2i-clickhouse` = the image pinned at
`tests/integration/compose.yaml:68` (`clickhouse/clickhouse-server@sha256:87e0a5b7…`, 25.8.33.6-alpine), db
`infrx_t2i`; `infrx-t2i-s3` = `pgsty/minio@sha256:b6bfe723…`, bucket `infrx-t2i`, prefix `test/t2i/<uuid>/`. Each
stack case creates its own database with `SYSTEM STOP MERGES`, so a duplicate stays physical and only `FINAL` hides it.

## Tests and the mutants naming them

| Case | Oracle | Mutants |
|---|---|---|
| `test_shipping_is_off_unless_…_all_set` | flag OFF | `flag_any_setting_enables`, `flag_ignores_the_bucket` |
| `test_a_crash_before_fsync_ships_only_the_promised_records` (T1 crash drill + torn tail, restart) | TRACE-RECOVER | `rows_never_inserted`, `torn_tail_not_reported`, `shipped_segment_never_acked` |
| `test_an_unsynced_record_in_the_active_segment_is_never_shipped` | TRACE-RECOVER | `active_segment_shipped` |
| `test_duplicate_shipping_after_a_lost_ack_is_one_logical_trace` | TRACE-RECOVER | `shipped_segment_never_acked`, `find_without_final` (stack) |
| `test_a_projection_outage_leaves_the_segment_for_the_retry` | TRACE-RECOVER (CH unavailable) | `segment_failure_escapes` |
| `test_metadata_ships_while_the_content_store_is_down_and_content_follows` | TRACE-RECOVER | `acked_without_its_content`, `a_failed_put_claims_stored`, `unstored_content_followed` |
| `test_the_projection_carries_the_versions_and_the_loss_state` | versions + loss state | `pins_looked_up_under_the_key_not_the_org`, `loss_state_dropped`, `rate_card_version_dropped` |
| `test_a_pins_lookup_outage_holds_the_segment` | TRACE-RECOVER | `segment_failure_escapes` |
| `test_a_segment_this_reader_cannot_read_is_never_acked` | no delete of unread bytes | `poison_segment_acked`, `unreadable_segment_acked` |
| `test_a_tenant_reads_only_its_own_content` | TRACE-TENANT | `key_from_the_envelope_ref`, `find_ignores_the_org` (stack) |
| `test_a_row_pointing_at_another_tenants_object_is_never_followed` | TRACE-TENANT (cross-org ref) | `cross_org_ref_followed` |

Failed-then-passed during the lane: mutants `rows_never_inserted` and `find_without_final` were first
`broken_runner` (the cases unpacked `[row] = …`, a `ValueError` death); the cases now assert the row count
(`one()`), and both are killed.

## Wiring requests

- **WR-1 (E2 DDL)** `tests/integration/harness.py`: add
  `def apply_trace_schema() -> None: clickhouse_client().command((API_DIR / "infrx/traces/ship/schema.sql").read_text())`
  and call it after `wait_all()` where the stack is brought up; proof in `tests/integration/test_services.py`:
  `harness.apply_trace_schema(); harness.apply_trace_schema(); assert harness.clickhouse_client().command("EXISTS TABLE trace_envelopes") == 1`.
  (A `docker-entrypoint-initdb.d` mount would not work: the image's init client runs without `--database`, so the
  table would land in `default`.)
- **WR-2 (Makefile:21)** append `tests/t/ship/test_mutants.py` to `api-mutants` (the stack-only pair skips visibly
  without `INFRX_T2I_STACK=1`).
- **WR-3 (composition, flag OFF, D5 integration)** where the gateway builds the trace sink: only when
  `ship.shipping_enabled(limits)`, build `Shipper(sink, ClickHouseProjection(clickhouse_connect.get_client(dsn=limits.clickhouse_url)), S3ObjectStore.connect(limits.s3_trace_bucket, "<prefix>/"), pins=<D5 lookup>)`
  and run `await sink.rotate(); await shipper.ship()` on a timer. The D5 lookup:
  `select model_id, requested_model, deployment_revision_id, serving_version_id, rate_card_version, policy_version from infrx.jobs where org_id = %s and request_id = %s and accounting_regime = 'credit'`
  -> `AdmissionPins | None`, raising `DependencyUnavailable` on a PG failure. Needs a PG-backed proof on a port
  (t2i has none; suggest `t2i: {"postgres": 57549}` in TASK_PORTS, or prove it in I2L/E5L). E4 regression before any hosted enable.
- **Ruling proposal (08 §5 / §10)**: the trace projection is `trace_envelopes` keyed `(org_id, "<segment>:<position>")`,
  version `content_stored`, read FINAL with the org bound; content object key `trace/{org_id}/{segment}:{position}` in
  `S3_TRACE_BUCKET`, derived by the shipper (the envelope's `content_ref` is not a key).

## Open issues

- `schema.sql` cannot be mutated by the shared runner (it compiles every mutated file as Python); its engine/key are
  proved through the stack cases (`find_without_final`, `find_ignores_the_org`).
- Poison/unreadable segments are held, not quarantined elsewhere: they stay on disk and count against the spool cap
  (visible as `held` + `spool_paused`). A quarantine move needs a spool API (T1 owner) if it ever occurs.
- Logical expiry/deletion of content is T3; `read_content` does not check expiry yet.
- `find` scans one org's rows by `request_id` (no skip index): `ponytail:` note in the adapter.

## Estimate (remaining for T2I to merge)

optimistic 1 h / likely 2 h / pessimistic 5 h, confidence medium. Basis: implementation and lane checks done in
about 2.5 h of lane time; remaining is one verify round plus WR-1..3 coordinator wiring (D5 PG lookup proof is the
largest unknown).
