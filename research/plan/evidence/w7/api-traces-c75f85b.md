# AP-07 (lane api-traces, wave 7) — evidence at c75f85b

Base `cd9f517c` · branch `codex/w7-api-traces` · head `c75f85b8` · key `ap7` (PG 57559, ClickHouse 57560/57561, MinIO 57562) · 2026-10-02.

## Decision: no migration 0063

Read 0057 (`infrx.trace_consent`), `gateway/capture.py` (`ConsentSource`, `effective`), 0003 (`consent_history`), 0005 (`api_keys.trace_mode`), 0027 (`lab_access_grants` + RPCs). The schema already carries every decision:

| Need | Carried by |
|---|---|
| key-level capture | `public.api_keys.trace_mode` (0005), read by the gateway through 0057 as min(key, org head) |
| consent version | `infrx.consent_history` (0003, append-only; a capture change appends the next version) |
| purpose grants with expiry/revocation/versions | `infrx.lab_access_grants` (0027) via `lab_put_access_grant` / `lab_revoke_access_grant` |

What was missing were the WRITERS for capture; `DataUse.put_capture` writes both rows in one transaction on the platform pool (`service_role`, like `PgAccessStore`/`PgTenantStore`). Purposes stay the frozen four (`DataPurpose`); annotation and export map to `training` (`GATE_PURPOSES`: export gate = training). Ceiling (ponytail): a dedicated `infrx_runtime` console login would need SECURITY DEFINER writers → 0063 then (schema request below, conditional).

## Changed paths

- `apps/infrx-api/infrx/console/data_use.py` (new; namespace package — `infrx/console/__init__.py` is api-identity's)
- `apps/infrx-api/infrx/gateway/routes/console_data_use.py` (new; R270 envelope incl. 422 via route-local `EnvelopeRoute`)
- `apps/infrx-api/infrx/gateway/routes/lab_traces.py` (projection/filter extension only; every existing tests/g/lab_traces mutant anchor kept)
- `apps/infrx-api/tests/ap07/` (conftest, test_data_use, test_trace_reads, test_trace_stack, mutants, test_mutants)

## Slices

- **07a done.** GET data-use, PUT keys/{id}/capture (CAS on consent version, state-based replay, head = highest live key mode, evaluation consent needs full), GET/POST data-grants (CAS on `grant_version`, replay 200/new 201, expiry on the DB clock), DELETE data-grants/{id} (replay-safe). Owner-of-the-actor's-org in a `session` actor only; extras refused (no grantor in a body); suspended org writes nothing.
- **07c done.** `/lab/v1/traces` items gain `access_state` (`not_captured | revoked | metadata | expired | partial | content`, one reason each, ladder in the module docstring; the existing `access` field unchanged so `apps/lab/lib/services/traces/port.ts` keeps working), `elapsed_ms` (admission → capture finish; null when unfinished, never 0; no per-component breakdown is stored, so none shown), pins `price_version` + `request_schema_version`; filters `serving_version_id`, `model_id` narrow the serving set BEFORE the projection is asked; a filtered cursor is `<position>.<digest>` and is refused under another filter. No object key / prefix / storage URL on the wire (test).
- **07b/07d done on the ap7 stack (lane-level composed proof): PASS.** `tests/ap07/test_trace_stack.py`: consent + grant through the data-use routes → `capture.build` (ConsentSource on 0057) → sync + SSE via the gateway hook, async via `JobCapture` → shipped to ClickHouse + MinIO → read via `lab.compose.lab_traces`: 3 rows, all `content`; the off key's answered request leaves no trace; duplicate delivery lists one per request; revocation between list and detail → `revoked`, no content; projection outage (table renamed away) → answer whole, segment held, ships exactly once after, a second pass ships 0.
- **Not done:** the E5L-box variant under `tests/integration/lab_observe/` (needs the e5l namespace/ports — not this lane's key; `observe_world.py` edits are a wiring request) → batch 2 (api-traces-2). Lost-ack at the worker and the box-level outage remain the existing t2f/E5L cases (o07/o09, `test_capture.py` lost-ack cases), not duplicated.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=ap7 pytest -q tests/ap07/test_data_use.py` before the implementation | 2 | RED: collection error, `infrx.console` absent (seam test first) |
| `INFRX_D_TASK=ap7 pytest -q tests/ap07/test_trace_reads.py` before the implementation | 1 | RED: 9 failed, 2 passed (the 2 = the no-object-key guard, already true) |
| `INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 pytest -q tests/ap07 tests/g/lab_traces tests/g/lab_auth tests/l/access tests/contracts/test_api_wire.py` | 0 | 120 passed, 2 skipped (lab_traces stack case without INFRX_LAB_API_STACK; one l/access skip) |
| `INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 pytest -q tests/ap07/test_trace_stack.py` | 0 | 2 passed |
| `INFRX_D_TASK=ap7 INFRX_AP7_PG=1 INFRX_MUTANTS=all pytest -q tests/ap07/test_mutants.py` | 0 | 50 passed: 47/47 mutants killed, well-formed, every-case, pristine |
| `INFRX_MUTANTS=all pytest -q tests/ap07/test_mutants.py` (fake half) | 0 | 22 passed: 19 projection mutants killed + 3 guards |
| `INFRX_MUTANTS=all pytest -q tests/g/lab_traces/test_mutants.py` | 0 | 28 passed (existing list, all anchors intact) |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 458 errors (baseline 458; no new) |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 1 | EXPECTED until wiring request W2: `api-mutants names apps/infrx-api/tests/ap07/test_mutants.py 0 times` |

Failed-then-passed during the work: two 07a mutants reported `broken_runner` once because a foreground PG run collided with the mutant copy on the one ap7 lock (HarnessBusy); rerun sequentially → killed. That collision is why the list gates its PG-only mutants on `INFRX_AP7_PG=1`.

## Wiring requests

- **W1 (gateway/app.py + pilot.py + config.py, AP-00's switch):** `DeploymentSettings.console_data_use: bool = False`; `pilot.build_ingress_deps`: `rt.data_use = DataUse(connect) if deployment.console_data_use else None` (connect = the adapters' service_role connector; `from ..console.data_use import DataUse`); `app.py`: add `console_data_use` to the `routes` import and to `ROUTERS`; mounted only with `rt.actors` (AP-01's `SessionActors`) present. Composed test: create_app with CONSOLE_DATA_USE on + a stub actor source → GET /console/v1/data-use 200 no-store; off → 404 (as `test_data_use__nothing_is_mounted_without_data_use` at the route level).
- **W2 (Makefile, api-mutants):** `	# AP-07's list (api-traces, LW7): its PostgreSQL half needs Docker, skips visibly without it; task-local key ap7` / `	cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap7 INFRX_AP7_PG=1 uv run --frozen pytest -q tests/ap07/test_mutants.py`. Composed test: `tests/integration/test_makefile_mutant_lists.py` green.
- **W3 (Makefile, lab-compositions):** `	# AP-07b/07d: the composed trace proof on the ap7 block (PG + ClickHouse + MinIO; containers per the file's header)` / `	cd $(API) && INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 .venv/bin/python -m pytest -q tests/ap07/test_trace_stack.py`.
- **W4 (batch 2, tests/integration/lab_observe/):** the e5l-box variant of the stack proof (data-use routes on the composed gateway) — needs W1 merged and an `observe_world.py` hook to mount `console_data_use`.

## Schema requests

None now. Conditional: if the console API runs on the dedicated `infrx_runtime` login, 0063 = SECURITY DEFINER `infrx.put_key_capture(jsonb)` (owner check, CAS, consent append + key update, the body of `DataUse.put_capture`) and `infrx.data_grants_of(p_org uuid)`; and the grant CAS moved into SQL.

## Open items / proposed rulings (unnumbered)

- Proposed ruling: "Trace access state is a separate field `access_state` beside the authorization field `access`; its ladder is lab_traces.py's; grant expiry and revocation both read `revoked` (permission withdrawn or lapsed), content past retention reads `expired`."
- Proposed ruling: "Data-use purposes are the frozen four; annotation and export are `training`; no new purpose without a contracts change."
- 0027 refuses a suspended organization's REVOCATION too (`lab_grant_version`): a suspended customer cannot withdraw a sharing grant through the API. Privacy-relevant; fix needs a SQL change (0063) or an operator path.
- `Idempotency-Key` is not stored: replay is state-based (identical decision = no new version); a same-key/different-body 409 needs 0060's `control_idempotency` (api-schema) — follow-up once on the base.
- `EnvelopeRoute` (422 as the R270 envelope with field_errors) is route-local; propose moving it into `gateway/control.py` (coordinator-owned keystone) for every new route.
- Nothing enabled by default: no route mounts without `rt.data_use`; `LAB_TRACES` unchanged.

## Estimate (remaining, AP-07)

optimistic 3 h / likely 5 h / pessimistic 9 h, confidence medium — basis: W1–W3 wiring review at merge (~1 h), the e5l-box variant W4 in batch 2 (~3 h, depends on W1 + observe_world hook), the suspended-revocation fix if ruled (0063, ~2–4 h).
