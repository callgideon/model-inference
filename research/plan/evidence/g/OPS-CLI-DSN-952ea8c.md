# OPS-CLI-DSN — the operator CLI's own DSN (G8/I8 follow-up, RL-V5)

- Base `feca50dd`; head `952ea8c` (code) on `codex/ops-cli-dsn`; evidence commit follows.
- Changed: `apps/infrx-api/infrx/operations/cli.py`, `apps/infrx-api/tests/g/ops/test_cli.py`,
  `apps/infrx-api/tests/g/ops/mutants.py`, `infra/rollout/README.md` (§3 + log),
  `infra/runbooks/rollout.md` (W7d + log).

## Design
- `build_operations(settings=None, *, environ=os.environ)`: dials `$OPERATIONS_DATABASE_URL`
  when set (stripped), else `settings`/`config.from_env()`'s `DATABASE_URL`. `main` passes
  its `environ` through. The variable is read in the CLI like `$INFRX_OPERATOR_KEY` (an
  operator credential input, not a runtime setting); `config.py`/`PilotSettings` (frozen
  contract) untouched, so `deploy.preflight.schema_id()` is unchanged (`c947948d1106e4fc`).
- Guard: `infrx.gateway.pilot.dedicated_login` imported lazily inside `build_operations`
  (no import cycle: pilot imports nothing from `infrx.operations`); helper not moved,
  `tests/g/test_composition.py` unchanged and green. A dedicated login (`infrx_runtime`,
  `infrx_monitor`, bare or `<role>.<ref>`) -> `SystemExit` naming the source variable and
  `OPERATIONS_DATABASE_URL`, before `connector()` is called. A DSN `conninfo_to_dict`
  cannot parse -> `SystemExit("<VAR> is not a valid connection string")` `from None`
  (no DSN fragment in the message or chained traceback).

## Commands
| cmd (in apps/infrx-api) | exit | result |
|---|---|---|
| `uv run --frozen pytest -q tests/g/ops/test_cli.py` with base `cli.py` (feca50dd) | 1 | 2 failed (both new cases), 5 passed — fail-before |
| same, head | 0 | 7 passed |
| `uv run --frozen pytest -q tests/g/ops tests/g/test_composition.py` (no docker) | 1 | 96 passed, 16 failed: every `*_pg.py` case, `HarnessBusy` on the default port 55432 (held by another lane); not touched |
| `INFRX_D_TASK=g8 uv run --frozen pytest -q tests/g/ops tests/g/test_composition.py` | 0 | 112 passed (task-local g8 postgres 55447; the PG world builds through `build_operations`) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/ops/test_mutants.py -k "operations_ or well_formed or every_case"` | 0 | 6 passed (4 `operations_*` mutants killed incl. the 2 new, list well-formed, every case covered) |
| `uv run --frozen pytest -q tests/g/ops/test_mutants.py` (subset) | 0 | 14 passed |
| `uv run --frozen pytest -q tests/i -k "readme or rollout or runbook or doc"` | 0 | 12 passed |
| `uv run --frozen python -c "from deploy import preflight; print(preflight.schema_id())"` | 0 | `c947948d1106e4fc` (file untouched) |

## Mutants (new)
- `operations_dedicated_login_admitted` (`if dedicated:` -> `if False:`): killed by
  `test_api_ops__the_operator_tool_refuses_a_dedicated_runtime_login` (pytest.raises did not raise).
- `operations_dsn_precedence_inverted` (the operator DSN read -> `""`): killed by
  `..._the_operator_dsn_takes_precedence_over_database_url` (dialled the wrong DSN) and
  `..._refuses_a_dedicated_runtime_login` (runtime DSN in the operator variable not refused).
- Existing `operations_without_a_database` anchor kept unique (one `if not dsn:`).

## Wiring requests
None required. Optional (I8): if the deploy env schema should name `OPERATIONS_DATABASE_URL`
it is operator-shell only and must NOT go into the runtime env file; no schema change proposed.

## Open issues
- Hosted verification: the box's operator shell must export `OPERATIONS_DATABASE_URL`
  before the env file's `DATABASE_URL` moves to `infrx_runtime` (coordinator).

## Estimate
Remaining: optimistic 0 h, likely 0.25 h (review), pessimistic 1 h; confidence high; basis:
code + tests + docs complete, all local suites green.
