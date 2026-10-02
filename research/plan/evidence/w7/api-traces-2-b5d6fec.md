# AP-07 remainder (lane api-traces-2, wave 7 batch 2) — evidence

Base `b05eb6f4` · branch `codex/w7-api-traces-2` · key `ap7` (PG 57559, ClickHouse 57560/57561, MinIO 57562) · 2026-10-02. Named after the first slice's head `b5d6fec`; later slices append below.

## Published protocol: `infrx.traces.eligible` (consumed by api-judge-2's `infrx/judge/start.py`)

```python
from infrx.traces.eligible import TraceEligible

eligible = TraceEligible(connect, retention)   # connect: state.jobstore.connector(dsn)
                                               # retention: T3 Retention; retention.traces = ClickHouseProjection
await eligible(grantor_org_id: str, model_id: str, limit: int) -> list[tuple[str, bool]]
```

- Matches `infrx.judge.start.Eligible = Callable[[str, str, int], Awaitable[Sequence[tuple[str, bool]]]]` exactly; `start.start` calls it with `(request["grantor_org_id"], request["model_id"], MAX_CANDIDATES)`.
- Answer: `(request_id, has_video)` pairs, unique, newest first, at most `limit`. `limit` outside 1..200 (`judge.dryrun.scan_bound`) → `errors.InvalidRequest`. PostgreSQL failure → `errors.DependencyUnavailable`.
- Offered only when: the grantor's CURRENT grant to the model's provider permits `external_judging` on `request_content` AND `response_content` for that model (database clock); the row is the grantor's on one of the model's `infrx.serving_versions`; `mode = 'full'`, `content_stored = 1`, inside T3's content bound, no `request`/`content` tombstone.
- `has_video`: the request's `infrx.job_media` includes a finalized `staged_media` row with mime `video/*`.
- Composition for the judge role (WR-2 of api-judge, `infrx/lab/workers/__main__.py:_judge`): `limits, retention = _traces(mode, env)` already exists there; `eligible = TraceEligible(connect, retention)`.

## Handback (head after this commit; code head 3e6c26b5)

### Changed paths
- `apps/infrx-api/infrx/traces/eligible.py` (new), `apps/infrx-api/infrx/console/data_use.py`, `apps/infrx-api/infrx/gateway/routes/console_data_use.py`
- `apps/infrx-api/tests/ap07/` (`test_eligible.py` new, `withdraw_door.sql` new FAKE, `conftest.py`, `test_data_use.py`, `mutants.py`, `test_mutants.py`)
- `tests/integration/lab_observe/test_ap07_box_variant.py` (new)
- `research/plan/evidence/w7/api-traces-2-b5d6fec.md`, `api-traces-2-WR-AP07B-1.patch`, `research/plan/evidence/coordinator/updates/AP-07-20261002T0445Z.json`

### Slices
1. **eligible (AP-08's read) — done.** Protocol above. Red first: `tests/ap07/test_eligible.py` collection error (exit 2, module absent); then 6/6 on the ap7 stack. Video signal = `job_media` x `staged_media.mime video/*` (the projection carries no modality).
2. **console_data_use on `control.R270Route` — done.** Route-local `EnvelopeRoute`/`invalid` deleted; the route's own try/except gone (R270Route renders every failure). New case `a_failure_is_an_envelope_never_a_trace` (500 envelope, no-store, request id, no exception text; 401 for no session) passes before and after (behaviour-preserving refactor). OpenAPI export regenerates byte-identical (`python -m infrx.contracts.openapi.export`, no diff).
3. **Suspended-org withdrawal — done against the FAKE door.** `DataUse.revoke_grant` checks the owner only and calls `infrx.lab_withdraw_access_grant` (api-schema-2's 0066 at c43a4aac, AP-07 section; args `{actor_user_id, grantor_org_id, recipient_provider_org_id}`). Until 0066 is on the base, `tests/ap07/conftest.py` re-implements `pg_world` and installs the door verbatim from `tests/ap07/withdraw_door.sql`. Red first: the new case answered 403. Every other write still refuses a suspended org (R33 case unchanged, green).
4. **e5l-box variant (W4) — written, BLOCKED.** `test_o11_data_use_decided_on_the_box_reaches_capture_and_the_lab`: the box gateway with `CONSOLE_DATA_USE`+`IDENTITY_API`+`LAB_TRACES`+`TRACE_PUMPS` and the worker; capture decided via `/console/v1` with a verified session; sync/SSE/async shipped once; beta decided `off` leaves 0 envelopes; Lab reads `content`, then `revoked` after the grantor's DELETE. Verdict **BLOCKED**: needs E5L's stack (tasklocal block 57100-57199, not ap7's key; isolation rule). Skips as `BLOCKED[e5l stack]` outside `INFRX_E2_NAMESPACE=e5l`. No `observe_world.py` edit is needed (its `lab_traces_env`/`supabase_door` compose it); registration as scenario o11 is WR-AP07B-1.

### Commands (exit, counts)
| Command | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 pytest -q tests/ap07/test_eligible.py` (before eligible.py) | 2 | RED: collection error |
| `INFRX_D_TASK=ap7 pytest -q tests/ap07/test_data_use.py -k "suspended or revocation"` (before the door) | 1 | RED: 1 failed (403 != 200), 2 passed |
| `INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 pytest -q tests/ap07 tests/g/lab_traces tests/g/lab_auth tests/l/access tests/contracts/test_api_wire.py` | 0 | 132 passed, 2 skipped |
| `INFRX_D_TASK=ap7 INFRX_AP7_PG=1 INFRX_AP7_STACK=1 INFRX_MUTANTS=all pytest -q tests/ap07/test_mutants.py` | 0 | 67 passed: 64/64 mutants killed (47 batch-1 - 1 retired + 2 withdrawal + 16 eligible) + well-formed + every-case + pristine; 24 min |
| `INFRX_D_TASK=ap7 INFRX_AP7_PG=1 pytest -q tests/ap07/test_mutants.py -k "well_formed or every_case"` (the Makefile W2 line's selection, no stack) | 0 | 2 passed |
| `pytest -m "not pg" tests/contracts/test_openapi_export.py tests/g/lab_traces tests/j tests/ap08 tests/contracts/test_config_and_imports.py` | 1 | 748 passed, 13 skipped, 1 failed: `tests/j/test_mutants.py::test_every_judge_module_is_covered` "no mutant touches ['start.py']" - PRE-EXISTING at the base (judge/start.py is AP-08 batch 1, api-judge-2's path; this lane touches nothing under infrx/judge) |
| `pytest tests/integration/lab_observe/test_ap07_box_variant.py` | 0 | 1 skipped: BLOCKED[e5l stack] |
| `pytest tests/integration/lab_observe/test_e5l_runner.py tests/integration/lab_observe/test_mutants.py tests/integration/test_lab_package_isolation.py` | 0 | 32 passed, 1 skipped (new file present, unregistered) |
| same two lab_observe files with WR-AP07B-1 applied in-tree (then restored) | 0 | 30 passed, 1 skipped; both new stack-mutant anchors occur exactly once |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 458 (baseline 458; an interim 459 from eligible.py's `pg_rows` union fixed in 3e6c26b5) |

### Wiring requests
- **WR-AP07B-1** (`tests/integration/lab_observe/runner.py`, `test_e5l_runner.py`, `mutants.py`): register the variant as E5L scenario **o11** - patch `research/plan/evidence/w7/api-traces-2-WR-AP07B-1.patch` (`git apply`): runner globs `scenarios_*.py` + `BOX_VARIANT`, `SCENARIOS["o11"]` (TRACE-BOUNDS, LAB-ACCESS), `REQUIRED["o11"]`; `test_e5l_runner` scenario_sources + `len == 11`; mutants' globs + `STACK_RUNNER` target + stack mutants `st_data_use_unmounted` (console_data_use `if service is None` -> `if True`) and `st_data_use_key_mode_unwritten` (data_use's api_keys update a no-op), both naming O11. Composed test: `runner.py --out <dir> --only o11` PASS on the e5l stack; `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e5l pytest tests/integration/lab_observe/test_mutants.py -k st_data_use` 2 killed.
- **WR-AP07B-2** (`Makefile`, lab-compositions, next to W3): `	cd $(API) && INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 .venv/bin/python -m pytest -q tests/ap07/test_trace_stack.py tests/ap07/test_eligible.py` and `	cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap7 INFRX_AP7_PG=1 INFRX_AP7_STACK=1 uv run --frozen pytest -q tests/ap07/test_mutants.py -k "el_ or well_formed or every_case"` (the eligible mutants need ClickHouse+MinIO; the api-mutants W2 line stays as is and deselects them visibly). Composed test: `tests/integration/test_makefile_mutant_lists.py` green.
- **WR-AP07B-3** (`infrx/lab/workers/__main__.py:_judge`, with api-judge-2's WR-2 start job): `from ...traces.eligible import TraceEligible`; after `limits, retention = _traces(mode, env)`: `eligible = TraceEligible(connect, retention)` passed to `start.start_pass(start.pg_queued(connect), wiring, eligible)`. Composed test: api-judge-2's worker proof with a real `TraceEligible` on a shipped trace (`tests/ap07/test_eligible.py`'s pattern).
- **At the 0066 merge** (coordinator): delete `apps/infrx-api/tests/ap07/withdraw_door.sql` and the `pg_world` override in `tests/ap07/conftest.py` (restore `pg_world` to the `tests.l.access.conftest` import). Composed test: `INFRX_D_TASK=ap7 pytest tests/ap07/test_data_use.py` green on the merged migrations.

### Schema requests
None new. Consumed: api-schema-2's `infrx.lab_withdraw_access_grant(jsonb)` (0066) - EXECUTE for the gateway's platform role (`service_role`), which `DataUse` runs as.

### Open items
- Hosted dependency: with the withdrawal on the 0066 door, `CONSOLE_DATA_USE` must not be switched on in production before 0066 is applied in an R151 window (the switch is off; batch 3).
- `eligible` reads `infrx.serving_versions`, `infrx.job_media`, `infrx.staged_media` and `lab_access_grants` (RPC) on the judge role's `LAB_DATABASE_URL` login; on the box that login must be able to read them (direct reads, like `PgServing`/`PgPins`; ponytail: a SECURITY DEFINER read in a later migration if the Lab login is narrowed).
- `eligible`'s tombstone filter runs after the LIMIT (ponytail comment): a deleted request shortens a page; fine at the judge's 200 bound.
- Retired mutant `field_errors_dropped` (the code it mutated moved to the keystone's `control.invalid`); `control.invalid`'s field_errors are the keystone's to guard.
- Proposed ruling (unnumbered): "A grantor's withdrawal of a data grant is never refused for suspension: revocation goes through the revoke-only door; every widening write stays refused for a suspended organization (R33)."

### Estimate (remaining, AP-07)
optimistic 1.5 h / likely 3 h / pessimistic 6 h, confidence medium - basis: WR-AP07B-1..3 review and the o11 run on the e5l stack by the coordinator (~1-2 h incl. a fix round if the box login lacks a grant), deleting the 0066 fake at merge (~0.2 h), WR-AP07B-3 composed with api-judge-2's start job (~0.5-1 h).
