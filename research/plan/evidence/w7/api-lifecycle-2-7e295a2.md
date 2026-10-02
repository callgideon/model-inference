# api-lifecycle-2 (LW7 batch 2) — AP-11 slice 11c (isolated mode) — evidence at 7e295a2

Lane api-lifecycle-2, branch `codex/w7-api-lifecycle-2`, worktree
`.claude/worktrees/codex-w7-api-lifecycle-2`, base `b05eb6f4`, code head `7e295a28`. Task-local
key `ap11` (PostgreSQL 57567, Valkey 57568, MinIO 57569; ClickHouse 57570/57571 only with
WR-AP11C-1 applied). Nothing hosted, no box, no AWS/SSM/S3, no Vercel, no secrets; `probe.py`
untouched and uncalled; never d1/55432.

## Commits

| Commit | What |
|---|---|
| 35d35b89 | stages 01/08/10/11 gain their AP-01/02/03 halves; 02/03 (AP-04) and 12-17 (AP-07/08) implemented; `composed` gating; minted keys in a 0600 file beside the state; pinned CAS versions; dry-run label; `unmounted` routes BLOCKED by name; layer-1 fake serves the wave-7 families (31 defects) |
| 01bd2d47 | `world.py`: the wave-7 gateway switches, the Lab control unit (third process), ClickHouse when ap11 has a port, API-minted keys; `Inference-Id` (the gateway's header; `X-Inference-Id` never existed); 57 mutants |
| 5d50d257 .. eb8dcfdc | runbook for 11c; world flags upserted (`lab_submission` has no row until set); refusal codes in evidence; 0029-shaped payer ref; stage 12's grant covers `feedback` (stage 16's review door requires it) |
| 00c6488f | a stage BLOCKED on its predecessor carries no label |
| 2d8b15fe | stages 04-07 by contracts.md §5/§6 (AP-05/06), proved on the fake; capacity_unavailable -> GPU-TARGET; a stage without a runner names why |
| 7e295a28 | cases hardened so every mutant dies by an assertion |

## Changed paths

Owned: `tests/integration/api_lifecycle/stages/` (`__init__.py`, `consumer.py`, `lab.py` new,
`hosting.py` new), `runner.py`, `world.py`, `infra/runbooks/api-lifecycle.md`. Also the
track's own test files `conftest.py`, `test_runner.py`, `mutants.py`, `test_mutants.py`
(deviation: the brief lists stages/runner/world; rule 7 requires the track's cases and
mutant list, which live there). Evidence: this file, the two verdicts below, the update JSON.

## What 11c adds

- **Composition is explicit.** Config `composed` names the AP packages the target serves; a
  stage none of whose routes is served is BLOCKED naming them and never called; a stage with a
  served half runs it (`Context.composed`). A route the target answers with FastAPI's own
  404/405 is `BLOCKED[<AP>] <route>: not mounted` (an R270 not-found envelope is not).
- **Keys through the API.** Stage 08 claims each individual's grant (a second claim replays) and
  mints keys with `POST /console/v1/keys`; the secret goes to `<state>.keys` (0600, reloaded on
  resume), the state keeps only ids. A replayed creation is the same key and never re-reveals
  the secret; a secret lost with its acknowledgement is revoked and one more key minted.
  Data-plane audience checks run in 08 (keys exist only from there): a key authenticates at
  `/v1/*` and is 401 at `/console/v1/me`; a consumer key at a private endpoint is refused.
- **Resume-safe CAS writes.** The consent and grant versions read before a CAS write are
  pinned in the state, so a resumed `PUT .../capture` / `POST /console/v1/data-grants` re-sends
  the identical body under its original key.
- **Stages.** 01 + AP-01 reads per actor (availability, `/console/v1/me`, capabilities,
  workspaces, members; outsider and consumer hold no workspace). 08 + grant/keys. 10/11 +
  AP-02: the console request's pins, `settlement_state=settled`, the reserve released, exactly
  one ledger debit equal to `charged`, B's console 404. 12: a fresh capture key, capture via
  `PUT /console/v1/keys/{id}/capture` (earlier keys stay off), a scoped grant
  (`provider_sharing` + `external_judging`, request/response content + feedback) persisted
  through the API, one captured sync call. 13: the Lab trace list/detail (same pins, the real
  captured text, timing never 0, the uncaptured earlier request absent, outsider refused).
  14: judge models truthful, a reviewed rubric, config, estimate (never a spend authority),
  PROVIDER_USD budget, run 202 + OperationDoc, replay = same run; dry run: nothing sent, nothing
  settled, nothing `scored`, label `dry-run: ...`. 15: consumer credits unchanged by judging,
  budget reserved/settled 0, a replayed run spends nothing. 16: a human review stored once
  (provenance human), read back apart from customer signals; calibration `insufficient`.
  17: grant revoked through the API, the trace drops to metadata/`revoked`, a follow-up judge
  run refused. 02/03 (AP-04, upload path) and 04-07 (AP-05/06, by contracts.md §5/§6) are
  implemented and proved on the fake; in the world they stay BLOCKED until composed.
- **Evidence.** Every refused exchange records its R270 `error.code` (never the message);
  stages on a stand-in carry `label`; only a stage that runs carries one.

## Commands, exit codes, counts

| Command (repo root) | Exit | Result |
|---|---|---|
| `apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/api_lifecycle/test_runner.py` with the new cases and the BASE runner/stages (implementation set aside) | 1 | **RED: 65 failed, 21 passed** |
| same, at 35d35b89 | 0 | 86 passed |
| `... tests/integration/api_lifecycle/` at 7e295a28 | 0 | 97 + 6 = **103 passed** (37 case functions; 54 product-defect params; well-formed + every-case + 2 subset mutants + 2 self-tests) |
| `INFRX_MUTANTS=all ... tests/integration/api_lifecycle/test_mutants.py` (first full run, 2d8b15fe) | 1 | 132 passed, 5 failed: `predecessor_any_status`, `minted_reported_as_fixture` survived; `minted_file_unwritten`, `minted_not_reloaded`, `unimplemented_stage_unexplained` died by an exception - all five fixed in 7e295a28 (8/8 rerun killed) |
| same, final full run at 7e295a28 | 0 | **137 passed** in 725 s: **133/133 mutants killed** (70 from 11a/11b re-anchored + 63 new), list well-formed, `test_every_case_is_covered_by_a_mutant` green (37 cases), both self-tests (no false kill) |
| `runner.py --mode isolated --world ap11` at 7e295a28 (committed tree) | 1 | **FAIL**: 01, 08-11 PASS; **12 FAIL** - `PUT /console/v1/keys/{id}/capture` 500 `internal` (SR-AP11C-1, a product defect of AP-07's `DataUse` on the gateway pool); 13-17 BLOCKED "needs stage 12"; 02-07, 18 BLOCKED naming AP-04 (not composed), AP-05/06 and 18's missing runner. 54 requests, 5 inference. `AP11-isolated-7e295a2-verdict.json` |
| same with WR-AP11C-1 + SR-AP11C-1 applied in the worktree (reverted after; `pins.dirty: true`) | 3 | **BLOCKED**: **01, 08-17 PASS** (14-15 labelled dry-run); 02-03 BLOCKED[AP-04] (api-artifacts-2's composition), 04-07 BLOCKED[AP-05/06], 18 BLOCKED. 87 requests, 6 inference. `AP11-isolated-7e295a2-with-WR-AP11C-1-SR-AP11C-1-verdict.json` |
| `make api-lint` | 0 | All checks passed; `uvx ruff@0.15.12 check tests/integration/api_lifecycle`: All checks passed |
| `make api-typecheck` | 0 | `pyright: 458 errors (baseline 458)` (the package is outside the pyright project) |
| `pytest tests/integration/test_makefile_mutant_lists.py tests/integration/test_lab_package_isolation.py` | 0 | 9 passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (915 local links across 482 documents) |
| grep `sk-infrx-…`/`eyJ…`/`Bearer`/`X-Amz-Signature`/the MinIO password in both verdicts | - | 0 matches |

Diagnostic runs on the way (each a real finding, fixed in the lane's code where it was the
runner's): the AP-07 `_Pooled` defect (12); `lab_submission` absent = off, which the judge
doors report as 503 "the judge store is unreachable" (14); a uuid5 payer id refused by 0029's
`lab_ref_parts` (14); the review door requires the `feedback` category in the grant (16).

## Isolated run - what it is and is not

Real: PostgreSQL (Supabase image, the 62 migrations on the base 0001..0064 (0060, 0061, 0064 LOCAL-ONLY), the PROVISIONAL
Marlin seed), Valkey, MinIO, ClickHouse (WR-AP11C-1 run), the gateway and worker in pilot mode
with IDENTITY_API, AUTH_FACADE, CONSOLE_READS, CONSOLE_ACTIONS_API, CONSOLE_DATA_USE (and
TRACE_PUMPS with ClickHouse), the Lab control unit with LAB_JUDGE_API and LAB_ARTIFACTS,
`fake_vllm.py`, real HTTP. Declared fixtures (in the verdict): verified sign-up + GoTrue
sessions (HS256 of the world's edge), the operator flags `signup_grant`, `credit_admission`,
`lab_submission`, the NemoStation administrator membership, the seeded listing, the judge's
dry run, and `lab_unit_actors`: AP-01's SessionActors stood in on the Lab unit, whose
composition leaves `actors=None` on this base (WR-AP11C-2). Not GPU, live-judge, hosted or a
complete-lifecycle result.

## Wiring requests

- **WR-AP11C-1 (`apps/infrx-api/infrx/contracts/tasklocal.py`, keystone)**: in `TASK_BLOCKS`,
  after the `ap7` entry:
  `    # AP-11c: the lifecycle runner's isolated world captures and reads traces (stages 12-17).`
  `    "ap11": {"clickhouse": (57570, (57571,))},`
  Composed test: `local_services("ap11")` names `infrx-ap11-clickhouse` on 57570 and
  `all_host_ports()` raises nothing; the world then composes TRACE_PUMPS + ClickHouse +
  LAB_TRACES and stages 12-13 run (the second verdict above).
- **WR-AP11C-2 (`apps/infrx-api/infrx/lab/control/app.py`, `_compose`; api-identity-2's Lab-unit
  mount)**: compose `actors = SessionActors(sessions, PgIdentity(connect)) if judge is not None
  else None` (imports `from ...console.session import PgIdentity, SessionActors`) and pass
  `actors=actors` instead of `actors=None`. On the hosted Lab login `PgIdentity` needs
  api-schema-2's 0065 identity functions (SR-AP01-1). Composed test: `create_app()` with the
  three `INFRX_LAB_*` + `LAB_JUDGE_API=1` and a GoTrue stub: `GET /lab/v1/judge/models` with a
  verified session is 200 (today 503 "the Lab session actors are not composed"). Once merged,
  the world's stand-in goes away by itself (it stands in only while `rt.actors is None`).
- **WR-AP11C-3 (Makefile `api-lifecycle`)**: none needed; the target and the mutant line from
  WR-AP11-1 run this lane's code unchanged (the full list now takes ~20 min).

## Schema / code requests (other owners)

- **SR-AP11C-1 (api-traces-2, `apps/infrx-api/infrx/console/data_use.py`)**: `DataUse.put_capture`
  uses `conn.transaction()` on `rpc.connection(self.connect)`; composed on the gateway pool the
  connection is `pilot._Pooled` (execute + close) and the call raises `AttributeError`
  -> `PUT /console/v1/keys/{id}/capture` 500. AP-07's own proofs used a plain connector.
  Fix (the one applied for the diagnostic run): an `_transaction(conn)` async context manager
  doing explicit `begin` / `commit` / `rollback` on `execute` alone (as `console.actions` does)
  and `async with rpc.connection(self.connect) as conn, _transaction(conn):`. Composed test:
  this runner's stage 12 on ap11 (FAIL -> PASS), or a `tests/ap07` case composing `DataUse`
  over `pilot.adapters_from_env`'s pool.
- **Open item (api-judge-2, `infrx/lab/judge_api/doors.py`)**: `SessionDoors.call` catches
  `OperationalError` before the domain mapping, so `require_feature`'s 55000 (a disabled flag)
  answers 503 "the judge store is unreachable" with retry_after 5. Proposed: map 55000 through
  `jobstore.domain_error` (maintenance) before the OperationalError catch.
- No DDL request.

## Deviations and open items

- Stage 14 reads `GET /lab/v1/judge/rubrics` (a reviewed version) instead of
  `POST /lab/v1/judge/rubrics`: creating a rubric version needs a reviewed definition (P-07),
  which api-judge-2 owns; the row's title stays verification.md's.
- AP-04 calls pass `?provider_org_id=` ("verified provider_org_id context", contracts.md §4);
  api-artifacts-2 decides the session actor's workspace - align s02/s03 if it differs.
- Stage 03 on a toy upload: an `unsupported` 422 is BLOCKED[artifact] naming the reasons (a
  supported revision needs the measured Marlin bytes), never a FAIL.
- Stages 04-07 bodies follow contracts.md §5/§6 field names; api-lifecycle-3 aligns them with
  AP-05/06's merged schemas. Stage 18 has no runner yet (named in its BLOCKED reason).
- The AP-01 workspace reads are called on the gateway origin (IDENTITY_API mounts them there);
  move to `lab` when api-identity-2 mounts them on the unit.
- Proposed ruling (unnumbered): "An AP-11 isolated verdict may PASS a judge stage only as a
  labelled dry run (`label` starts `dry-run`), never as a judge result; only a live run with
  P-10 configured reports scores."

## Estimate (remaining AP-11 work after this lane)

11c (this lane) done pending review: 0.5 / 1.5 / 3 h (review fixes; re-run once WR-AP11C-1/2
and SR-AP11C-1 merge). 11d/11e (batch 3, live target, coordinator-serialized): 8 / 14 / 26 h,
confidence low - basis: 02-07 need api-artifacts-2/api-hosting/api-publication merged and
body alignment (~1-2 h per stage on this framework), plus live-target setup, P-10, a GPU
window and cleanup.
