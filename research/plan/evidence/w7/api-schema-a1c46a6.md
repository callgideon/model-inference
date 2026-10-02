# api-schema (AP-00 slice 00d): 0060 control operations — evidence at a1c46a6

Lane api-schema, wave 7 (LW7), branch `codex/w7-api-schema`, worktree `.claude/worktrees/codex-w7-api-schema`, base `cd9f517c`, head `a1c46a6b`. Task AP-00, slice 00d only. Task-local key `ap0` (PostgreSQL 57550; containers `infrx-ap0-postgres`, `infrx-ap0-postgres-supabase`, both removed at exit). Nothing hosted, no box, no AWS, `hosted-migrate.sh` untouched.

## Changed paths (all owned)

| Path | What |
|---|---|
| `apps/app/supabase/migrations/0060_control_operations.sql` | new, LOCAL-ONLY (R271's 0060): `infrx.control_operations`, `infrx.control_idempotency`, `control_op_start/lease/advance/finish/cancel/get/pending`, helpers `control_owner`, `control_op_doc`, `control_op_row`; header ROLLBACK lines |
| `apps/app/supabase/migrations/README.md` | the wave-7 allocation table (0060–0064, owners and conditions, from R271) |
| `apps/infrx-api/infrx/state/control_ops.py` | new: the `ControlOps` protocol, `Operation`/`Started` records, `PgControlOps` (over `state.rpc`), `FakeControlOps`, `input_hash` |
| `apps/infrx-api/tests/d/test_control_ops_units.py` | the protocol's 8 scenarios (run on the fake here), `input_hash`, `PgControlOps`'s own half on a recording connection |
| `apps/infrx-api/tests/d/test_control_ops.py` | the same 8 scenarios on PostgreSQL + privileges + an 8-way race + a raw-audience check (11 checks) |
| `apps/infrx-api/tests/d/test_control_ops_upgrade.py` | the R151 rehearsal: `deploy/migrate.py` plan/apply over hosted history at 0059, re-run, ROLLBACK lines, roll forward |
| `apps/infrx-api/tests/d/test_control_ops_mutants.py` | 59 SQL mutants (0060) + 43 Python mutants (`control_ops.py`); every check and unit case named |

`tests/i/test_migrate.py` did not need its pin changed (it hard-codes no range; 6 passed).

## The `ControlOps` protocol (published for api-artifacts and api-hosting)

Module `infrx.state.control_ops`; method names fixed by the wave-7 plan. `PgControlOps(connect)` and `FakeControlOps(now=...)` obey the same rules: one scenario set runs on both.

```python
input_hash(body) -> "sha256:<hex>"          # sha256 of codec.compact_bytes(body)

class Operation(api.Wire):                   # .doc() -> api.OperationDoc (R270's wire form)
    operation_id, kind, state, phase, resource_kind, resource_id, actor: api.Actor,
    created_at, updated_at, retry_after_s, error: api.ErrorBody | None,
    fence: int, lease_owner, lease_until, cancel_requested_at
class Started(api.Wire): operation: Operation; replayed: bool; outcome: dict | None

class ControlOps(Protocol):
    async def start(kind, actor, idempotency_key, input_hash, *, resource_kind=None,
                    resource_id=None, outcome=None, retention_s=86400) -> Started
    async def lease(operation_id, owner, ttl_s) -> Operation
    async def advance(operation_id, fence, phase, retry_after_s=None) -> Operation
    async def finish(operation_id, fence, state, error=None) -> Operation
    async def cancel(operation_id, actor) -> Operation
    async def get(operation_id, actor) -> Operation
    async def pending(kinds, limit=25) -> tuple[str, ...]
```

| Rule | Refusal |
|---|---|
| Key scope = the actor's tenant (provider workspace, else organization, else user) + `kind`. Same key + same hash replays the first operation and its `outcome` | — |
| Same key, different hash | `IdempotencyConflict` 409 |
| Key past its retention (≥ 24 h, `retention_s`) | `IdempotencyExpired` 410, never a second operation; the operation stays readable |
| Kind not dotted lower case (`deployment.create`), hash not sha256, key not 1–255 chars, retention < 86400, actor with no tenant | `InvalidRequest` 422 |
| New operation: `queued`, fence 0 | — |
| `lease`: ttl 1–3600 s, owner 1–128 chars; a free or expired lease is granted with fence + 1 (`queued` → `running`; `cancel_requested` stays); the live holder renews under the same fence | another holder's live lease or a finished operation: `Conflict` 409; bad ttl/owner: 422 |
| `advance` / `finish`: only the live lease's fence; `advance` never changes the state; `finish` → succeeded / failed / cancelled, `error` iff failed, clears the lease | stale/expired/finished: `Conflict` 409; bad state or error: 422 |
| `cancel`: `queued` → `cancelled`; `running` → `cancel_requested` (first instant kept); repeated cancels answer the row | succeeded/failed: `Conflict` 409 |
| `get` / `cancel` read authority: the owning tenant or an operator actor | anyone else, unknown id, non-uuid id: `NotFound` 404 |
| `pending(kinds, limit)`: oldest unfinished operations of those kinds no live lease holds; limit clamped to 1–100 | — |
| Database unreachable | `DependencyUnavailable` 503 (retryable), message without the driver text |

## Decisions against the brief's wording (one line each)

- No outbox table: `infrx.outbox` does not fit (its `kind` enum and `org_id` belong to the job relay; `gc_outbox` reaps by job state), and a `control_outbox` would duplicate the operation row. An unfinished, unleased operation is the pending work; `pending()` is the controller's discovery after a restart; a domain SQL function that must commit its own row with the operation calls `infrx.control_op_start` inside its own transaction.
- RLS: on, with no browser policy and no browser grant (browsers have no `usage` on `infrx`, R271). "Operator and owning actor read" is enforced in `control_op_get`/`control_op_cancel` for the actor FastAPI established.
- Writers: `service_role` (0004's default, the gateway's broad login) and `infrx_lab_control` (the Lab control unit). Not `infrx_runtime`: its grant set is pinned catalog-wide at 45 functions (`tests/d/checks_reads.RUNTIME_FUNCTIONS`, `checks_operator`); the first gateway route that needs control ops adds it there.
- `advance` takes no `state` argument: the only non-terminal transitions are `lease`'s and `cancel`'s, and a worker that could write the state could clear a cancellation.
- `idempotency scope` is derived in SQL (tenant + kind), not passed by the caller, so a lane cannot mis-scope a key.

## Commands (exit codes, counts)

Red seam run, before `control_ops.py` existed:

```
INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_control_ops_units.py tests/d/test_control_ops.py
-> exit 2: ModuleNotFoundError: No module named 'infrx.state.control_ops' (2 collection errors)
```

Failed-then-passed regressions found on the way: JSON `null` for `error`/`outcome` was stored as a jsonb `null` (every success refused) — fixed with `nullif(..., 'null')`, now mutant `cto_json_null_error`; the raw-audience check was vacuous (the body lacked `retention_s`, so it was refused for the wrong reason; mutant `cto_audience_unchecked` survived) — fixed with a positive control, now killed; granting `infrx_runtime` broke `checks_reads`' pinned runtime set — grant removed.

| Command (from `apps/infrx-api`) | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=ap0 uv run --frozen pytest -q -rfE tests/d/test_control_ops.py tests/d/test_control_ops_units.py tests/d/test_control_ops_upgrade.py tests/d/test_schema_postgres.py tests/d/test_reads.py tests/d/test_operator_d10.py tests/d/test_credit_schema.py tests/d/test_upgrade_d10.py tests/d/test_upgrade_split.py tests/d/test_upgrade_lab.py tests/d/test_l3sql_control.py tests/d/test_l2sql_access.py tests/d/test_composition_pg.py tests/l3sql/test_lw7.py tests/l3sql/test_lw9.py tests/i/test_migrate.py tests/i/test_known_good_proof.py` (at a1c46a6's tree) | 1 | 175 passed, 3 xfailed, 2 failed: `test_upgrade_lab` (0060's two tables not in its NEW_TABLES) and `test_known_good_proof::…hosted_migrate_expects_exactly_the_migrations_after_its_anchor` (EXPECTED_PENDING pins the whole tree) — both outside my paths, wiring requests W2/W3 below |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap0 uv run --frozen pytest -q -rA tests/d/test_control_ops_mutants.py` | 0 | 105 passed: 3 guards (well-formed, no superseded anchor, every case covered) + 59 SQL killed + 43 Python killed; 0 survivors |
| `INFRX_D_TASK=ap0 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q -rfE tests/d/test_control_ops.py tests/d/test_control_ops_upgrade.py tests/d/test_control_ops_mutants.py --deselect tests/d/test_control_ops_mutants.py::test_code_mutant_is_killed` | 0 | 74 passed: 11 checks + the rehearsal + 3 guards + 59 SQL mutants killed on `supabase/postgres@sha256:7768d0d1…` (PostgreSQL 17.6, container `infrx-ap0-postgres-supabase`) |
| `uv run --frozen pytest -q tests/i/test_migrate.py` | 0 | 6 passed |
| `make api-lint` (repo root) | 0 | All checks passed |
| `make api-typecheck` (repo root) | 0 | pyright: 458 errors (baseline 458), no new error |
| `tests/integration/test_makefile_mutant_lists.py` (repo root, pinned interpreter) | 1 | 7 failed, all one cause: `tests/d/test_control_ops_mutants.py` named 0 times — wiring request W1; with W1's patch applied to a scratch copy (`INFRX_E2_REPO_ROOT`): 7 passed |
| W2 probe: `test_upgrade_lab.py` copied with W2's NEW_TABLES patch, run on ap0, then deleted | 0 | 7 passed |
| W3 probe: the patched assertion run against this tree and the three `tests/i/mutants.py` anchors of that test | — | pristine passes; `hosted_migrate_pending_short`, `_anchor_stale`, `_post_check_stale` each still fail it |
| `python3 research/plan/scripts/validate_plan.py` (repo root, with this file) | 0 | PASS (914 local Markdown links across 468 documents) |

The upgrade rehearsal (`test_control_ops_upgrade.py`, plain and Supabase): 0001–0026 + consumer history (6 seeded job states, `test_upgrade_d10.seed_history`) → 0027–0059 → history table holding 0001–0059 → `deploy/migrate.py plan` lists exactly 0060 → `apply --expect <digest>` exit 0, history at 0060 → 96 existing tables' counts, money sums, the jobs' identity/money, every existing relation/column/function ACL unchanged; +2 tables (0 rows), +10 functions → re-apply is a no-op → the header's ROLLBACK lines restore the exact 0059 snapshot → re-apply equals the first apply and a `start` works.

## Wiring requests

- **W1 (Makefile, `api-mutants`)**: after the 0058 line (`… tests/d/test_code_mutants_lw9.py`) add:

  ```
  	# 0060's SQL list + control_ops.py's Python list (api-schema, AP-00 00d, R271): the SQL needs Docker, skips visibly without it; task-local key ap0
  	cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_control_ops_mutants.py
  ```

  Composed test: `tests/integration/test_makefile_mutant_lists.py` → 7 passed (measured on a scratch copy with this patch).
- **W2 (`apps/infrx-api/tests/d/test_upgrade_lab.py`)**: its Lab set is every file from 0027, so 0060's tables are new there. Replace `              "infrx.lab_variant_identities"}                               # 0058` with

  ```
              "infrx.lab_variant_identities",                               # 0058
              "infrx.control_operations", "infrx.control_idempotency"}      # 0060 (R271)
  ```

  Composed test: `INFRX_D_TASK=<key> uv run --frozen pytest -q tests/d/test_upgrade_lab.py` → 7 passed (measured on ap0). 0061/0062 add their tables the same way at their merge.
- **W3 (`apps/infrx-api/tests/i/test_known_good_proof.py::test_ops_recover__hosted_migrate_expects_exactly_the_migrations_after_its_anchor`)**: R269 keeps `hosted-migrate.sh` unedited until the window, so the pin "EXPECTED_PENDING = every file after the anchor" fails as soon as any wave-7 file exists. Proposed body after `files = …`:

  ```python
      expected = re.search(r'^EXPECTED_PENDING="([^"]*)"', text, re.M)[1].split(", ")
      after = [f for f in files if f[:4] > at]
      # R269/R271: a wave-7 file (0060-0064, migrations/README.md) stays LOCAL-ONLY after the
      # window's set until its own window edits these lines
      assert [f[:4] for f in after[:len(expected)]] == expected, (expected, after)
      assert all("0060" <= f[:4] <= "0064" for f in after[len(expected):]), after[len(expected):]
      newest = after[len(expected) - 1][:-4]
      assert f'case "$POST" in *"{newest[:4]} {newest[5:]}"$\'\\n\'"nothing pending") ;;' in text
  ```

  Composed test, to run once applied: `uv run --frozen pytest -q tests/i/test_known_good_proof.py` green and `INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/test_mutants.py -k hosted_migrate` 3 killed. Not run here (the file is not mine); the W3 probe above shows the patched assertion passing on this tree and failing under each of the three mutants. Alternative: the coordinator edits `hosted-migrate.sh` at the next window instead; then this test needs no change.

## Schema requests

None (this lane owns 0060). For api-artifacts (0061) and api-hosting (0062): call `infrx.control_op_start(jsonb)` from inside a domain function when the domain row and the operation must commit together; their own Lab routes run on `infrx_lab_control`, which already executes the seven boundary functions.

## Proposed ruling (unnumbered)

Control operations (0060): the `Idempotency-Key` scope of a long operation is the actor's tenant (provider workspace, else organization, else user) plus the operation kind, derived by the store and never passed by a caller; a key past its retention (at least 24 h) answers 410 `idempotency_expired` and never starts a second operation; the operation row is its own outbox (`pending()` is the discovery), and `infrx_runtime` gains the boundary only with the first gateway route that uses it.

## Open items

- api-schema re-proves the whole 0060+ set on the merged tree after batch 1 (R271).
- W2/W3 must land with the merge, or `make check`'s tests/d and tests/i fail on the merged tree.

## Estimate

Remaining for AP-00 00d: optimistic 1 h, likely 2 h, pessimistic 4 h; confidence medium. Basis: the slice is done at a1c46a6 (brief 4/7/12, about 6 h spent); what remains is the coordinator's W1–W3 and the post-merge re-proof of 0060–0062 (rebuild + this list on ap0, plain and Supabase, about 15 min of machine time per pass), plus one review round.

## Log

- 2026-10-02: written at head a1c46a6 by the api-schema lane.
