# AP-10 (lane api-improve, wave 7 batch 1): slices 10a and 10b

- Base `cd9f517c` (keystone). Branch `codex/w7-api-improve`. Slice commits `2def683f` (10a) and `47ab6e9a` (10b). The evidence commit follows them.
- Key `ap10`: PostgreSQL 57566 only (`INFRX_D_TASK=ap10`, container `infrx-ap10-postgres`, removed at exit). The dev endpoint fake ran on a free loopback port. This lane touched no d1 or 55432 container, no hosted service, no box and no secret. The 27 `[pg]` errors in `tests/l/control` on the default key are the harness refusing another checkout's `infrx-d1-postgres` (`ForeignContainer`). The same errors occur at the base.
- Nothing is enabled. No switch was added. The eval worker stays off. No migration was written.

## Changed paths (all owned)

- `apps/infrx-api/infrx/lab/evaluation/__init__.py` (new): `Experiments`, `Subscriptions`, `Catalog` and `evaluation_ports(connect)`.
- `apps/infrx-api/tests/ap10/`: `test_evaluation_ports.py` (unit, 8), `test_evaluation_ports_pg.py` (ap10 PG, 4), `test_row27.py` (unit, 2), `test_row27_pg.py` (ap10 PG, 21), `mutants.py` (17), `test_mutants.py`.

## 10a: the evaluation read composition

The SQL already existed: 0043 `lab_put_experiment`/`lab_experiments`/`lab_checkpoint_listing`, 0034 `lab_evaluator` and D8 `PgCheckpointLedger`. Nothing composed it. The E6L/LAB-E2E backends (`apps/lab/tests/b/backend.py`, `apps/lab/tests/e2e/evaluate/backend.py`, `tests/g/lab_evaluations/test_lab_evaluations_pg.py`) used the route suite's in-memory fakes. This slice adds no table, no RPC and no second store.

- **experiments**: the adapter is written over 0043. 0043 keys an experiment by its two published run records, but the route fixes the launch clock first (`put` then `freeze`). `put` therefore publishes both run records (the same payloads that `freeze` publishes again as a replay) and stores the pair.
  - A resubmit returns the first launch with its first `created_at`.
  - Another launch under the same id is a 409.
  - When two first launches race, the loser returns the winner's row.
  - The listing rebuilds each launch from its records. This is N+1; a `ponytail:` comment records it.
- **ledger**: D8's ledger, with 0043's nested listing flattened to the route's row.
- **catalog**: the evaluator spec by ref is real (0034). The launch listing needs harness, evaluator and serving SQL that does not exist yet, so it returns an honest 503 naming SR-AP10-1. As a result, POST /experiments is also 503 until SR-AP10-1. POST /subscriptions works.
- **RunLedger.run_rows/checkpoint_rows** (pipelines): no listing SQL exists (`lab_external_runs`, `lab_checkpoint_receipts`). The honest 503 stays in `compose.py` as written ("WR-LAB2-4"); SR-AP10-2 is the exact SQL it waits on.

Red at the base: the same route over the base composition (`LabEvaluations(sessions, access, store=store)`), with an authorized member of a provider that has nothing, returned:

```
BASE experiments 503 {'refusal': 'unavailable'}
BASE runs 503 {'refusal': 'unavailable'}
BASE subscriptions 503 {'refusal': 'unavailable'}
BASE catalog 503 {'refusal': 'unavailable'}
```

Green with the ports composed:
- experiments, runs and subscriptions each return 200 `{"data": []}` (no-store).
- catalog returns 503.
- A stranger gets 404.
- A database that does not answer gives 503, never an empty 200. Covered by the unit cases and by the PG case against a dead DSN.
- A stored experiment (0043 row `created_by`=DEV with B2's protocol digest) and a subscription (201) read back through the routes from SQL on ap10.

## 10b: register row 27 (WR-C6-B1-FLAKE), root cause

**Reproduced on ap10 with the worker's own composition**, using the w case's exact sequence (`w.composed`, Dying endpoint, revoke, `lab recover`, redelivery). Over 25 runs at the original timing, run 2 charged two keys:

```
CALLS 2 ['attempt:…066:0000005a-…-000000000001:1:0', 'attempt:…066:0000005a-…-000000000002:1:0']
```

The two keys belong to two cases, each at attempt 1, call 0.

**Deterministic reproduction.** Both attempts are held in flight until each has been charged. The w file's own oracle (`tests/w/test_worker_lab_eval_pg.py:105`) then fails every time:

```
KEYS ['attempt:…12c:…000000000001:1:0', 'attempt:…12c:…000000000002:1:0']
>       assert len(c.wallet.calls) == 1
E       AssertionError: assert 2 == 1
```

**Root cause.** `worker.__main__` composes `Runner` with `LAB_EVAL_LIMITS.concurrency == 2`, so two cases are leased at once. The test's oracle was copied from B1's drill, which runs with `concurrency=1`. Each in-flight attempt is charged once by the endpoint under its own `Idempotency-Key`, `attempt:<run>:<case>:<n>:<call>`. A real gateway settles per key (`ingress.py:169`, `idempotency(...)`). Whether the second request lands before the first crash tears the loop down is timing, so only 1–2 runs in 20 show it. No attempt is ever charged twice, and no key is debited twice. The killed attempts are `expired` with no D7 cost. That gap is B1's documented one: at most one paid attempt per concurrent worker per kill, bounded by `max_attempts`.

**Fix.** The authoritative accounting boundary is correct, so there is no product change and no schema need. The defect is the oracle. The corrected oracle (`test_row27_pg.charged_once_each`) passes:
- once deterministically;
- 20 times at the original timing;
- in the fake world, where every key is debited once (`N + 2` keys and `N + 2` answers) and D7 records the `N` finished attempts.

The patch to the w oracle is WR-AP10-3, because that file is not this lane's.

**The real-process kill/restart oracle (E6L j09) was not rerun.** It needs the e6l stack (block 57200–57299: PG, MinIO, the worker process). That block is not this lane's key, so the rerun is BLOCKED for this lane. The coordinator command, unchanged, is `apps/infrx-api/.venv/bin/python tests/integration/lab_evaluate/runner.py --only j09`.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10` | 0 | 40 passed |
| `INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10 tests/g/lab_evaluations tests/b/runner/test_runner.py` | 0 | 93 passed, 1 skipped (b3-only `_pg`) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap10/test_mutants.py` | 0 | 19 passed: 17 mutants, 0 survivors; well-formed and every-case green |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 458 errors (baseline 458; none new) |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 1 | 7 failed: `tests/ap10/test_mutants.py` is not in api-mutants. With WR-AP10-4 applied transiently: 7 passed |
| WR-AP10-1 applied transiently: `pytest tests/g/test_startup.py -k lab_api_2` | 0 | 3 passed |
| WR-AP10-1 applied transiently: `INFRX_MUTANTS=all pytest tests/g/test_mutants.py -k "lab_evaluations_* or well_formed or every"` | 0 | 10 passed, including the new `lab_evaluations_ports_unwired` |
| WR-AP10-1 applied transiently: `pytest tests/g/test_startup.py tests/i/lab_control tests/l/control tests/g/lab_evaluations` | 1 | 120 passed, 5 skipped, 27 errors. Identical at the base: `[pg]` refused on a foreign d1 container |

## Wiring requests

- **WR-AP10-1** (`apps/infrx-api/infrx/lab/compose.py`, `tests/g/test_startup.py`, `tests/g/mutants.py`). In `lab_evaluations_pipelines_releases`:
  - Add `from .evaluation import evaluation_ports`.
  - Replace `LabEvaluations(sessions, access, store=store)` with `LabEvaluations(sessions, access, store=store,\n **evaluation_ports(connect))`.
  - Composed test: in `test_lab_api_2__the_lab_surfaces_are_composed_from_settings_only_when_enabled`, add `ap10 = {"experiments","ledger","catalog"}` for lab_evaluations, plus a type check `(ev.Experiments, ev.Subscriptions, ev.Catalog)`.
  - g mutants: re-anchor `lab_evaluations_without_d7` to `"LabEvaluations(sessions, access, store=store,"` and add `lab_evaluations_ports_unwired`.
  - Full diff: the handback. Verified transiently (above).
  - This also composes on the Lab control unit (`lab.control.app`). Its login lacks `lab_put_experiment`, `lab_checkpoint_listing`, `lab_list_datasets`, `lab_evaluator`, `lab_checkpoint_subscribe` and `lab_checkpoint_decisions`. Until SR-AP10-3 grants them, those reads answer 503 there; the code does not fake around this.
  - The LAB-E2E evaluate probe (`apps/lab/tests/e2e/evaluate/backend.py:80`, `composed = {name: … is not None}`) would count `catalog` as carried. It should call `catalog.catalog(...)` and treat `DependencyUnavailable` as not carried, so that j10 stays NOT RUN[SR-AP10-1] rather than binding.
- **WR-AP10-2** (`infrx/gateway/routes/lab_evaluations.py:198`): `x.port("experiments").put(who.provider_org_id, {...}, actor=who.user_id)`. Without it, the adapter's `put` refuses with 503 rather than publishing as nobody. The catalog 503 hides this until SR-AP10-1.
- **WR-AP10-3** (`apps/infrx-api/tests/w/test_worker_lab_eval_pg.py:105`): replace `assert len(c.wallet.calls) == 1` with `keys = [x["key"] for x in c.wallet.calls]; assert 1 <= len(keys) <= worker_main.LAB_EVAL_LIMITS.concurrency and len(set(keys)) == len(keys)`. Row 27: one paid attempt per in-flight case, none twice.
- **WR-AP10-4** (`Makefile` api-mutants line 45): append ` tests/ap10/test_mutants.py`.

## Schema requests (no SQL written here)

- **SR-AP10-1**:
  - (a) `infrx.lab_eval_catalog(p_args jsonb) returns jsonb`, SECURITY DEFINER, stable, provider-scoped. It returns `{datasets: [{ref, label}], harnesses: [{ref, harness_id, version, adapter}], evaluators: [{ref, label}], servings: [{ref, label}]}`:
    - datasets: from `lab_records` kind `dataset`;
    - harnesses: kind `harness`, `body::jsonb->>'adapter'`;
    - evaluators: `infrx.lab_evaluators`;
    - servings: `infrx.lab_serving_ref(deployment_revision_id)` over the provider's ready private dev deployment revisions.
  - (b) `infrx.lab_experiments` (0043) re-created to also return `baseline_run_ref` and `candidate_run_ref`. An experiment stored before `freeze` created its runs is otherwise unreadable, and its resubmit is a 503.
  - The adapter then reads both with no other change.
- **SR-AP10-2**: pipeline listings `infrx.lab_external_runs_of {provider_org_id}` → `[{external_run_id, doc}]` and `infrx.lab_checkpoint_receipts_of {provider_org_id}` → `[{checkpoint_id, external_run_ref, artifact_digest, state, reason (P3 note)}]`. This is register row 15 / WR-LAB2-4.
- **SR-AP10-3**: grant `infrx_lab_control` EXECUTE on the six functions above (R251: the pin in `tests/d/test_code_mutants_lw8.py` moves with it).

## Proposed ruling (unnumbered)

Register row 27 is not a double debit. The eval worker's in-flight bound is `LAB_EVAL_LIMITS.concurrency` paid attempts per kill, each debited once by the endpoint's per-key settlement and recorded `expired` with no D7 cost. Row 27 closes with WR-AP10-3 and E6L j09 rerun unchanged. LAB_EVAL_WORKER enablement stays a separate decision.

## Open / not done

- E6L j09 rerun: BLOCKED for this lane, because it needs the e6l block.
- 10c–10e are batch 2/3.
- POST /experiments stays 503 until SR-AP10-1 and WR-AP10-2.

## Estimate (remaining for 10a/10b closure)

- Hours: 2 / 4 / 8 (optimistic / likely / pessimistic).
- Confidence: medium.
- Basis: the wiring is verified transiently. SR-AP10-1/2/3 are small SQL plus pins in the schema owner's file. The adapters already read both new fields, so (b) needs a one-line adapter change. The j09 rerun is about 15 minutes on the e6l stack.
