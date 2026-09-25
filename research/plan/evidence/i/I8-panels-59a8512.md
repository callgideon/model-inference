# I8-panels (OB-10 regression) - base 59a8512e

Defect: WR-I8-2 (f8c70b55) declared seven `infrx_db_pool_*` families with no dashboard panel,
breaking I3B OB-10 (`shown == set(FAMILIES)`).

Change: `infra/alerts/dashboard.json` - one row "Is the database pool saturated?" with a panel per
family, split only on `process` (and `state` for connections; closed vocabularies). Metric
families untouched. No alert rule added: OB-10 does not require one, and a rule needs a runbook
section, a fault case and a measured (or P-18-marked) threshold - left to a follow-up.

| command | exit | result |
|---|---|---|
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/recovery/test_observe.py` (before, base) | 1 | 1 failed (ob10), 15 passed |
| same (after) | 0 | 16 passed |
| `cd apps/infrx-api && INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_observe.py` | 0 | 13 passed, 1 xfailed (pre-existing F4 xfail) |
| manual mutant: delete the "DB pool timeouts" panel, ob10 | 1 | killed: `families with no panel: {'infrx_db_pool_timeouts_total'}` (restored) |

No docker used. No mutant added to tests/i/mutants.py: its cases must be tests/i cases and OB-10
lives in the I3B suite; the anchor there is `mutants_i3b.py` i3bm24 (I3B-owned, unchanged, still
applies). Estimate remaining: 0 h (rule optional: 1/2/3 h, medium).
