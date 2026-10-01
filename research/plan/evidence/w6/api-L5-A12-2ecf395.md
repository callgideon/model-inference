# W6 api-L5 — A12: the mechanical splits (engine_wire.py, traces/segment.py)

Lane api-L5, branch `codex/w6-api-L5`, base `2add8e0a`, commits `43358ff3` (split) and
`2ecf3958` (runner self-test anchors). Key `w5` (task-local PG 55445 / Valkey 55491).
Behaviour-preserving: code moved verbatim, old modules re-export every moved name.

## What moved (line counts before -> after)

| module | before | after |
|---|---|---|
| `infrx/worker/engine.py` | 1275 | 1043 (A14/A13 edits included) |
| `infrx/worker/engine_wire.py` (new) | — | 253 |
| `infrx/traces/spool.py` | 1151 | 912 |
| `infrx/traces/segment.py` (new) | — | 261 |

- `engine_wire.py`: `prepared_request`, `cache_salt`, `media_uuid`, `check_storage_ref`,
  `_inside_tenant_root`, `local_media_url`, `_encodable`, `_SHORT_ESCAPES`, `_json_cost`,
  `_split_encoded`, `_delta_payload`, `_parse_usage` + the constants only they use
  (`STORAGE_REF_SEGMENT/PATTERN`, `LOCAL_MEDIA_SCHEME/FILE`). `engine.py` imports them back
  (one import block, `# noqa: F401`), so `infrx.worker.engine.X` is the same object.
- `segment.py`: the format constants (`SEGMENT_VERSION/MAGIC/PREFIX/SUFFIX`, `HEADER`, `FRAME`,
  `LENGTHS`, `MAX_ENVELOPE_BYTES`), `SpoolIO`, `segment_header`, `frame_checksum`,
  `pack_frame`, `frame_size`, `Scan`, `scan_segment`, `_torn`, `segment_names`, `recover`.
  `spool.py` keeps the writer (`SEGMENT_MAX_BYTES`, `CAPTURE_PRUNE_AT`, `_nothing`, the
  in-flight dataclasses, `SpoolCapture`, `SpoolTraceSink`) and re-exports the rest;
  `infrx/traces/__init__.py` is unchanged.

## Tests first

- New cases: `tests/w/test_engine.py::test_api_stream__the_wire_helpers_live_in_one_module_the_adapter_reexports`,
  `tests/t/test_trace_spool.py::test_the_segment_format_lives_in_one_module_the_sink_reexports`
  (identity of every moved name through both modules).
- RED before the split: `pytest -k reexport` -> `2 failed` (no `engine_wire` / `segment`
  module). GREEN after: `2 passed`.
- New mutants (each kills its case by assertion, `INFRX_MUTANTS=all -k reexport`):
  `tests/w/mutants.py::wire_helper_dropped_from_the_reexport` (drops `_parse_usage` from the
  re-export), `tests/t/mutants.py::a_segment_name_dropped_from_the_reexport` (drops
  `frame_size`). First T attempt dropped `segment_names` and was a broken copy
  (`infrx/traces/__init__` imports it -> collection error); re-pointed to `frame_size`: killed.

## Anchors re-pointed (rule 3)

A scratch checker imported every `tests/**/*mutants*.py` list and counted each
`worker/*`/`traces/*` anchor in its file: after the move 51 entries (50 distinct mutants)
named text that had left `engine.py`/`spool.py`; all re-pointed, 677 anchors checked, 0 bad.

| list | re-pointed | how |
|---|---|---|
| `tests/w/mutants.py` | 24 | `EW = "worker/engine_wire.py"` |
| `tests/w/loop_mutants.py` | 12 | `EW` |
| `tests/w/w3_mutants.py` | 1 (`unset_root_is_guessed`) | `EW` |
| `tests/t/mutants.py` | 13 | `_m(..., file=SEGMENT)` (new kwarg, default `SPOOL`) |
| `tests/t/test_trace_mutants.py` runner self-test | 3 inline mutants | `file=mutation_list.SEGMENT` |

The file guards in `tests/w/test_mutants.py` and `tests/w/test_loop_mutants.py` admit
`worker/engine_wire.py`. No anchor was replaced by a different edit: same `old`/`new`
text, new file.

## Commands (head `3cb8d2fc` code; `2ecf3958` = + the self-test fix)

| command | exit | result |
|---|---|---|
| `INFRX_D_TASK=w5 pytest -q tests/w tests/t tests/m/test_prepare.py --ignore-glob='*mutants*'` | 0 | 519 passed, 38 skipped (visible: other keys' stacks — T2I/T3/T2F ClickHouse, I2B-R4 S3, r1/r2/p2 PG keys, WR-T-4 t2f) |
| `INFRX_D_TASK=w5 INFRX_MUTANTS=all pytest -q tests/w/test_mutants.py tests/w/test_loop_mutants.py tests/w/test_w3_mutants.py tests/w/test_w4_mutants.py tests/t/test_trace_mutants.py` | 1 | 520 passed, 1 failed = the T runner self-test (`class SpoolIO:` anchor moved) -> fixed in `2ecf3958`, rerun `-k "runner_cannot or well_formed"` 2 passed. 0 survivors. |
| `INFRX_D_TASK=w5 INFRX_MUTANTS=all pytest -q tests/w/test_w5_mutants.py tests/w/test_worker_main_mutants.py tests/w/test_prep_worker_mutants.py tests/w/test_lab_workers_mutants.py tests/t/ship/test_mutants.py tests/t/feedback/test_mutants.py tests/t/retention/test_mutants.py` | 0 | 445 passed, 27 skipped (S3-only 15: no MinIO on `w5`; ClickHouse/T3-store-only 12). The two skipped service.py PG mutants anchor lines this lane did not edit. 0 survivors. |
| `INFRX_D_TASK=w5 pytest -q` the 13 other suites importing `infrx.worker`/`infrx.traces` (n/lineage pg, r/optimization, g/relay_readiness_pg, g/lab_traces, g/trace_export, content, m/pilot_media, m/retention, d/composition_pg, i/worker_unit, i/packaging, i/observe, g/test_startup) | 0 | 252 passed, 49 skipped, 1 xfailed (first attempt: 28 `HarnessBusy` on the w5 lock held by a `/tmp/w5-pg-mutant-*` copy from another run of `test_w5_mutants`; nothing touched; rerun green) |

Not run: `tests/t/capture/test_mutants.py` (its make target pins `INFRX_D_TASK=t2f`, not this
lane's key; its anchors were in the checker: 0 bad). `make api-test` whole: it defaults to the
`d1` key (55432), which wave rule 5 forbids; the subsets above are the reason-stated substitute.

Isolation note: one focused run (`tests/w/test_service.py test_prep_worker.py
test_worker_main.py test_engine.py`) was started without `INFRX_D_TASK`; its three `_pg`
cases hit pgharness's foreign-container refusal for `infrx-d1-postgres` (another checkout's)
and failed without starting, stopping or connecting to it; rerun on `w5`: 119 passed, 7 skipped.

## Estimate (remaining for A12)

0 / 0.5 / 2 h (confidence high): only a merge-time anchor conflict if another lane edits
the moved helpers.
