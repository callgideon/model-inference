# G4T — Owned trace export HTTP adapter (lane `content`, LW3)

- Base `eb0734d7`; C2 `ae7375e7`; G4T `ecfaf2a3` (branch `codex/w5-content`).
- Tasklocal key `g4t` (PG 57508): unused this round - the export reads ClickHouse (T2I/T3), not PostgreSQL,
  and `g4t` has no ClickHouse block. `make api-test` ran on `INFRX_D_TASK=lab-c2` (57505).
- Status: **route implemented and UNMOUNTED; the mount is WR-G4T-1 behind `TRACE_EXPORT_API` (default off),
  proven as a patch; real-ClickHouse proof of the page query pending (WR-G4T-3).** Integration waits on C2
  (C2-RPC) per the manifest; the export itself uses T2I/T3 (merged) and no C2-RPC call.

## What was built

| Path | What |
|---|---|
| `apps/infrx-api/infrx/gateway/routes/trace_export.py` | `GET /v1/traces` (`register(app, rt, export=None)`): identity first; operator → 403; unknown or repeated query parameter (e.g. an `org_id`) → 400 before any read; `since`/`until` zoned ISO instants; `limit` 1..1000 (default 100); store call bounded by `DEPENDENCY_BOUND_S` |
| `apps/infrx-api/infrx/content/__init__.py` (G4T half) | `OwnedExport.page` (own org only, T3 `verdicts`: deleted / metadata-expired dropped, content deleted or past its bound → `expired`; R47 rows: no storage key, no handle, no content; opaque `(started_at, trace_id)` cursor; a short page is the end), `ClickHouseTracePages` (org, cursor, window, limit all query parameters), `build_export(limits)` |
| `apps/infrx-api/tests/g/trace_export/{test_trace_export,mutants,test_mutants}.py` | 15 cases, 27 mutants |
| `research/plan/evidence/g/G4T-ecfaf2a-wiring.patch` | WR-G4T-1 (not applied on the branch) |

Response: `{"data": [wire.TraceExport...], "next_cursor": <opaque>|null}`, `Inference-Id` header.

## Commands (exit codes, counts)

| Command | Exit | Result |
|---|---|---|
| seam first, route moved aside: `pytest -q tests/g/trace_export/test_trace_export.py::test_trace_export__an_org_exports_its_own_traces_only` | 4 | `ImportError: cannot import name 'trace_export' from 'infrx.gateway.routes'` |
| `uv run --frozen pytest -q tests/g/trace_export/test_trace_export.py` | 0 | 15 passed |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/trace_export/test_mutants.py` | 0 | 31 passed: 27/27 killed, well-formed, every case covered, 2 self-tests |
| WR-G4T-1 applied: `pytest -q tests/contracts/test_config_and_imports.py tests/g/test_startup.py tests/i/test_packaging.py tests/g/trace_export` | 0 | 359 passed |
| WR-G4T-1 applied: `python -m tests.g.mutants` (the 5 re-anchored router mutants + 3 new) | 0 | 8/8 killed |
| WR-G4T-1 applied: `pytest tests/g/test_mutants.py tests/i/test_mutants.py tests/contracts/test_mutants.py -k "well_formed or every_case or covered"` | 0 | 6 passed |
| WR-G4T-1 applied: `ruff check infrx tests/g tests/contracts tests/i deploy` | 1 | 11 findings, all pre-existing in files the patch does not touch (`deploy/replay_usage.py`, `tests/i/test_ops_steps.py`, `tests/i/test_rollout.py`) |
| `git apply --check research/plan/evidence/g/G4T-ecfaf2a-wiring.patch` (on ecfaf2a) | 0 | applies |
| `make api-test` (`INFRX_D_TASK=lab-c2`, at ecfaf2a, patch not applied) | 1 | 50 failed, 5064 passed, 99 skipped, 9 xfailed, 8 errors (1:43:34); every failure is a shared lock held by another lane: 7 tests/d/test_outbox_relay[valkey] (HarnessBusy: /tmp/infrx-d2-valkey-55463.lock), 8 errors tests/i/{test_observe,test_pooler,test_privilege_probe,test_rollback_drill} (BlockingIOError on the i8 pooler lock), 43 tests/i/test_mutants (pristine baseline fails on those same tests/i errors: the known non-default-key baseline). Rerun alone: 7 failed, 41 passed, 8 errors - the same locks, still held by other lanes. No failure in tests/content, tests/g/trace_export or any file this lane touched |

Mutants the runner revealed and fixed: `Inference-Id` asserted with `headers[...]` died by `KeyError`
(now `.get`); the export's own `content_live` check was redundant with T3's `verdicts` (the mutant survived;
removed - `verdicts` is the one rule); an outage of the tombstone store must be a 503 too (the case now takes
both the projection and T3 down).

## WR-G4T-1 (composition; `research/plan/evidence/g/G4T-ecfaf2a-wiring.patch`)

`infrx/config.py` `trace_export_api: bool = False` (`TRACE_EXPORT_API`) and `DEPLOYMENT_MUST_BE_POSITIVE`
skipping a bool; `gateway/app.py` `ROUTERS = (health, models, ingress, uploads, jobs, trace_export, metrics)`;
`gateway/pilot.py` `build_ingress_deps(..., trace_export=None)` puts `rt.trace_export` only when the switch is on,
from the injected export or `content.build_export(settings.pilot)`, and refuses to start (`RuntimeMisconfigured`
naming `CLICKHOUSE_URL`) when on without ClickHouse; `deploy/preflight.py` `NOT_SETTABLE["TRACE_EXPORT_API"]`;
tests: the router-name pin (`tests/contracts/test_config_and_imports.py`), `DEPLOYMENT_EXPECTED
["TRACE_EXPORT_API"] = False` (+ bool handling), `tests/g/test_startup.py`
`test_trace_tenant__the_trace_export_is_mounted_only_when_the_deployment_enables_it` (off: no route, 404 even
with an export; on: `GET` only, answers through the export; on without ClickHouse: refusal), `tests/g/mutants.py`
(router anchors + `composition_root_drops_trace_export`, `trace_export_switch_ignored`,
`trace_export_enabled_without_clickhouse`), `tests/i/test_packaging.py` (the edge's catch-all reaches `GET
/v1/traces`), `08 §5.1` row. **Conflicts textually with WR-G4F-1** (same `ROUTERS`, bool handling, preflight and
test lines): apply after it and keep both switches (`ROUTERS = (..., jobs, feedback, trace_export, metrics)`);
the bool edits are identical.

## Other wiring requests

- WR-G4T-2: `Makefile` `api-mutants` += `tests/g/trace_export/test_mutants.py`.
- WR-G4T-3: real ClickHouse proof of `ClickHouseTracePages` (a `g4t` ClickHouse block in `TASK_BLOCKS`, or the
  coordinator runs the export over T3's stack at integration); until then the tenant binding is proven on a
  recording client only.
- Hosted enable stays off until T3 + C2 are live and E4 reruns (plan rule 5).

## Proposed ruling (unnumbered)

- `GET /v1/traces` is JSON `{data: TraceExport[], next_cursor}` (not 05 §7.2's NDJSON): metadata only, no content
  handle; limit 1..1000 default 100; opaque cursor over `(started_at, trace_id)`; `since`/`until` zoned; unknown or
  repeated parameters 400; operator 403; T3 decides what is exported.

## Deviations / open issues

- JSON envelope instead of NDJSON (one cursor field, like `FeedbackList`); the `key` filter of 05 §7.2 is not
  implemented (add when asked).
- Export rows never carry a `content_handle`: the owner reads content through a future owner route; providers
  through C2 refs.

## Estimate (remaining for G4T)

optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium - basis: coordinator applies WR-G4T-1 after
WR-G4F-1 (the G4F fix-round analogue), one ClickHouse proof run, one review round.

## Fix round (2026-09-28; review findings 0-F1, 1-CONTENT-L1-1)

- **0-F1** (cursor past rows T3 dropped): new case `test_trace_export__the_cursor_advances_past_dropped_rows`
  (4 rows, row 2 deleted through T3, `limit=3`: page 1 is `[0, 1]` with a `next_cursor`, page 2 is `[3]`) and
  mutant `cursor_counts_exported_rows` (`len(rows) == limit` -> `len(out) == limit` in
  `infrx/content/__init__.py`) in `tests/g/trace_export/mutants.py`. Commit b59e47d3. No code change: the real
  code already counted rows read.
- **1-CONTENT-L1-1** (stale WR-G4T-1): the patch is regenerated on the current `claude/consumer-v1` tip
  **b4147d5c** (which carries WR-G4F-1). Both switches kept (`FEEDBACK_API`, `TRACE_EXPORT_API`);
  `ROUTERS = (health, models, ingress, uploads, jobs, feedback, trace_export, metrics)`; the duplicate
  `(str, bool)` hunk is dropped (the tip already has it); the five `tests/g/mutants.py` composition mutants are
  re-anchored on the G4F-patched `ROUTERS` line, plus the three G4T mutants. It no longer applies to this
  branch's base eb0734d7 (by design: it targets the tip).

| command (tree: detached b4147d5c + merge of b59e47d3 + the patch, `uv sync --frozen --all-extras`, `INFRX_D_TASK=g4t`) | exit | result |
|---|---|---|
| `git apply --check G4T-ecfaf2a-wiring.patch` on b4147d5c + b59e47d3 | 0 | applies cleanly (branch merge also clean) |
| `pytest tests/contracts/test_config_and_imports.py tests/g/test_startup.py tests/i/test_packaging.py tests/g/trace_export tests/content` | 0 | 394 passed, 22 skipped |
| `pytest tests/g -k "not pg"` | 0 | 765 passed |
| `pytest tests/g -k pg` (g4t PG 57508; a stale `infrx-g4t-postgres` from the previous dispatch's scratch tree was removed first: the first run refused it as ForeignContainer, 20 failed + 4 errors, all that refusal) | 0 | 24 passed, 4 skipped |
| `INFRX_MUTANTS=all pytest tests/g/trace_export/test_mutants.py` (lane worktree) | 0 | 28/28 mutants killed; 32 passed |
| `python -m tests.g.mutants` (whole G list, tip tree) | 0 | 404/404 killed (includes the 5 re-anchored and 3 new composition mutants) |
