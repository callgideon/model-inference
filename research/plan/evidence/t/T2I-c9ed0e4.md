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

## Fix round (review finding 1-T2I-R1), code head `9c1b9ce8`

Finding: the D5 version pins were proved only against a fake `pins(org_id, request_id)`; the real lookup existed
only as raw SQL inside WR-3, with no owner path and no PostgreSQL proof. Fakes never count (plan §2 rule 2).

**Gate accounting (unchanged by this round):** T2I's *versions-carried* claim does **not** count toward the LW1 gate
until WR-4 below is applied on the integration branch and `tests/t/ship/test_pins_pg.py` runs there unskipped.
Everything else in T2I (TRACE-RECOVER, TRACE-TENANT, flag OFF) is unaffected.

### What changed (owned paths only)

- `tests/t/ship/test_ship.py`: the versions case is now `versions_scenario(projection, objects, lookup, credit, pins, lost)`,
  reusable with any lookup, and it also ships **another organization's envelope claiming the CREDIT request's id**
  (must get no pins). `pins_for` is built on a new `recorded(lookup)` wrapper. The case name and its three mutants
  are unchanged.
- `tests/t/ship/test_pins_pg.py` (new): the same scenario rerun with D5's real `PgJobStore.admission_pins` over the
  `tests/d` harness (`tests/g/ops/pgworld.world`, all 26 migrations, the admission seed), on both projection backends:
  a request admitted through the store's own `admit_credit` ships with the pins PostgreSQL persisted
  (`admission.pins`), a legacy USD request (`admit_legacy`) and the other org's claim ship with none; and
  `test_a_postgresql_failure_holds_the_segment` (a store whose database does not exist): the lookup raises, the segment
  is held, nothing is inserted. Skips visibly, naming WR-4, while `PgJobStore.admission_pins` is absent.
  G8's `test_*_pg.py` pattern: outside the mutant runner (a PG case in a mutant copy would contend for the harness
  port's lock, and the default copy carries no migrations), so its oracles are the fail-first runs recorded below.
  `case_names()` reads only `test_ship.py`, so `test_every_case_is_covered_by_a_mutant` is unaffected.
- `infrx/traces/ship/` is unchanged: the `PinsLookup` seam was already right; what was missing was the real lookup.

### WR-4 (replaces WR-3's raw SQL): the D5 pins lookup + t2i's PostgreSQL port

Owner: D5 (lab-sql lane) or coordinator wiring. Not applied on this branch (LANE-RULES 2); applied only in the working
tree for the proof below, then reverted with `git apply -R` (`git status` afterwards: only the two owned test files).
`git apply --check` passes on `9c1b9ce8`. Patch (sha256 of the file form `87410969…6573a`):

```diff
diff --git a/apps/infrx-api/infrx/contracts/tasklocal.py b/apps/infrx-api/infrx/contracts/tasklocal.py
index f2aeafb3..1bc80e5b 100644
--- a/apps/infrx-api/infrx/contracts/tasklocal.py
+++ b/apps/infrx-api/infrx/contracts/tasklocal.py
@@ -89,6 +89,9 @@ TASK_PORTS: dict[str, dict[str, int]] = {
     "p3": {"postgres": 57530, "protocol": 57531},
     "r1": {"postgres": 57532, "valkey": 57533}, "r2": {"postgres": 57534},
     "g5": {"postgres": 57535}, "i4": {"postgres": 57536},
+    # T2I-R1 (WR-4): the trace lane's PostgreSQL, proving D5's pins lookup; its ClickHouse
+    # and S3 are its TASK_BLOCKS entry below.
+    "t2i": {"postgres": 57549},
     # The Lab E gates compose the E2 stack in their own blocks (TASK_BLOCKS below); the
     # PostgreSQL port is the one the harness derives, 55532 + the block's offset, as e3c's.
     "e3l": {"postgres": 57032}, "e5l": {"postgres": 57132}, "e6l": {"postgres": 57232},
diff --git a/apps/infrx-api/infrx/state/jobstore.py b/apps/infrx-api/infrx/state/jobstore.py
index 18d8935b..9a248d21 100644
--- a/apps/infrx-api/infrx/state/jobstore.py
+++ b/apps/infrx-api/infrx/state/jobstore.py
@@ -256,6 +256,16 @@ class PgJobStore:
             raise errors.NotFound(f"job {job_handle} is not a CREDIT job")
         return admission_v2_of(doc), _outcome(doc["outcome"])
 
+    async def admission_pins(self, org_id: str, request_id: str) -> AdmissionPins | None:
+        """T2I's pins lookup (`traces.ship.PinsLookup`): the pins a CREDIT request was
+        admitted at - the admission document's, which `load_work_credit` validates - for the
+        organization that owns the job only. None for a legacy USD job, another
+        organization's request or no job; a PostgreSQL failure raises, never None."""
+        rows = await self._query(
+            "select infrx.job_admission(j.request_id)->'pins' from infrx.jobs j "
+            "where j.org_id = %s and j.request_id = %s", (org_id, request_id))
+        return AdmissionPins.model_validate(rows[0][0]) if rows and rows[0][0] else None
+
     async def lookup(self, org_id: str, idem: IdempotencyRef):
         """R91 (`ports.JobStore.lookup` / `CreditJobStore.lookup`): one read-only statement
         over D2's idempotency mapping (0018 `infrx.idempotency_lookup`) - `(admission,
diff --git a/apps/infrx-api/tests/contracts/test_config_and_imports.py b/apps/infrx-api/tests/contracts/test_config_and_imports.py
index d804c417..b9dbe47d 100644
--- a/apps/infrx-api/tests/contracts/test_config_and_imports.py
+++ b/apps/infrx-api/tests/contracts/test_config_and_imports.py
@@ -472,8 +472,8 @@ LAB_LANE_PORTS = {
     "p3": {"postgres": 57530, "protocol": 57531}, "r1": {"postgres": 57532, "valkey": 57533},
     "r2": {"postgres": 57534}, "g5": {"postgres": 57535}, "i4": {"postgres": 57536},
     # T lanes: a block, because a TASK_PORTS entry would inherit the track's native 59000
-    "t2i": {"clickhouse": 57540, "s3": 57542}, "t2f": {"clickhouse": 57543, "s3": 57545},
-    "t3": {"clickhouse": 57546, "s3": 57548},
+    "t2i": {"clickhouse": 57540, "s3": 57542, "postgres": 57549},     # + T2I-R1 WR-4
+    "t2f": {"clickhouse": 57543, "s3": 57545}, "t3": {"clickhouse": 57546, "s3": 57548},
 }
 
 
```

- `admission_pins` reads the `'pins'` of `infrx.job_admission` (0018/0021: the same document `load_work_credit`
  validates at jobstore.py:447), org-bound as `_owned_doc` is; JSON null for a legacy USD job gives None. No new SQL
  object, no migration.
- `t2i` postgres **57549**: TASK_PORTS (a TASK_BLOCKS edit would move the anchor of `lw0_t_lane_inherits_the_track_clickhouse`);
  free, inside the Lab band, outside every E-gate block; the LW0 band pin must move with it (third hunk).
- WR-3 composition then reads `pins=<the gateway's PgJobStore>.admission_pins` (no raw SQL in the composition root),
  still only when `ship.shipping_enabled(limits)`; the E4 regression before any hosted enable is unchanged.
- After WR-4, `test_pins_pg.py` runs in `make api-test` on the default D harness, like `tests/g/ops/test_*_pg.py`.
- Suggested follow-up for the D owner (not required by this finding): name `admission_pins` in D's code-mutant list
  with the three defects below.

### Commands (from `apps/infrx-api` unless stated)

| # | Command | Exit | Result |
|---|---|---|---|
| F1 | `uv run --frozen pytest -q -rs tests/t/ship/test_ship.py tests/t/ship/test_pins_pg.py` (branch, no WR-4) | 0 | 11 passed, 12 skipped: 9 stack skips + 3 `test_pins_pg` skips naming WR-4 |
| F2 | WR-4 applied: `INFRX_D_TASK=t2i uv run --frozen pytest -q -rs tests/t/ship/test_pins_pg.py` | 0 | 2 passed, 1 skipped (stack half): real PG `infrx-t2i-postgres` on 57549, removed at exit |
| F3 | F2 with `admission_pins` defect **ignore_org** (`where j.request_id = %s` only) | 1 | FAILED at test_ship.py:395: the other org's `(ORG_B, request)` row carries the pins |
| F4 | F2 with defect **never_answer** (`return None`) | 1 | FAILED at test_ship.py:388: the admitted row has `(None, None, None)` |
| F5 | F2 with defect **swallow_failure** (`except Exception: return None`) | 1 | FAILED at test_pins_pg.py:67: `ShipReport(shipped=1, …)`, the row shipped without versions |
| F6 | WR-4 applied, stack up: `INFRX_T2I_STACK=1 INFRX_D_TASK=t2i uv run --frozen pytest -q -rs tests/t/ship/test_ship.py tests/t/ship/test_pins_pg.py` | 0 | 23 passed (20 ship incl. the stack half + 3 PG, both backends) |
| F7 | `INFRX_T2I_STACK=1 INFRX_MUTANTS=all uv run --frozen pytest -q -rs tests/t/ship/test_mutants.py` | 0 | 24 passed: 19/19 killed, well-formed, every-case-covered, 3 runner self-tests |
| F8 | WR-4 applied: `uv run --frozen pytest -q tests/contracts/test_config_and_imports.py` | 0 | 286 passed (LW0 band pin with t2i postgres 57549) |
| F9 | WR-4 applied: `INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py -k "lw0 or well_formed or every_case"` | 0 | 6 passed (the four LW0 tasklocal mutants still killed) |
| F10 | WR-4 applied: `uv run --frozen pytest -q tests/d/test_pgharness.py -k decoy`; root `apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_harness.py -k overlaps` | 0 / 0 | 1 passed; 9 passed (57549 intrudes on no namespace block) |
| F11 | WR-4 applied: `uv run --frozen ruff check infrx/state/jobstore.py infrx/contracts/tasklocal.py tests/contracts/test_config_and_imports.py` | 0 | clean |
| F12 | branch (WR-4 reverted): `INFRX_T2I_STACK=1 uv run --frozen pytest -q -rs tests/t` | 0 | 94 passed, 3 skipped (the WR-4 skips) |
| F13 | branch: `uv run --frozen pytest -q tests/t/ship/test_mutants.py` (default subset); `uv run --frozen ruff check tests/t/ship` | 0 / 0 | 11 passed; clean |

F3-F5 were run by editing only `admission_pins` in the WR-4 working tree and restoring it after each run. Stack for F6/F7:
`infrx-t2i-clickhouse` (image pinned at `tests/integration/compose.yaml:67`) on 57540/57541, `infrx-t2i-s3`
(`pgsty/minio@sha256:b6bfe723…`) on 57542; both removed after the runs. `make api-test` was not rerun: the branch
change is two files under `tests/t/ship`, and F12 covers the whole `tests/t` tree.

### Open after this round

- WR-4 is the gate for the versions claim (coordinator or lab-sql lane). WR-1 and WR-2 unchanged; WR-3 now reads
  `pins=jobs.admission_pins`.
- Earlier open issues unchanged (schema.sql not runner-mutable, poison/unreadable held not quarantined, T3 expiry,
  `find` scan).

### Estimate (remaining for T2I to merge)

optimistic 0.5 h / likely 1.5 h / pessimistic 4 h, confidence medium. Basis: this round took about 1 h of lane time;
what remains is coordinator wiring (WR-4 three hunks, WR-1..3) plus one `make check`, where the PG proof now runs.
