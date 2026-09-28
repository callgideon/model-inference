# P4 swap (WR-P4-1, adapter half) — lane lab-ui-swap, LW5

- Branch `codex/w5-lab-ui-swap`, base `ae72957f`, task commit `3dea0f83`, head `a408d8ff` (checks at the head). Key `p1` (57527) only, `INFRX_D_TASK=p1`; no container left. (The first stack attempt found p1 held by `codex-w5-lab-sql-integration-2`'s run: pgharness refused and altered nothing; rerun once it was free.)

## Changed paths (owned)
- `apps/lab/lib/services/pipelines/server.ts` (new): `labPipelines(env)` — `LAB_PIPELINES_API_URL` server-only, unset = `null`; token = `sessionToken(config)` (evaluation/server.ts).
- `apps/lab/lib/services/pipelines/http.ts`: per-call token (none: nothing sent); every renamed (camelCase) record checked — Label, Disagreement, ImportReceipt, LabelExport (+ lineage methods, omissions), TrainingRun (USD as exact strings, `costUsd` null-or-string, settled flag, holdout pin), Checkpoint (+ evaluation); a bundle that is not a JSON object is unavailable. Refusals: 401/403 denied, 404 not_found, 409 conflict, 410 gone, 422 invalid, else unavailable. Enum lists from port.ts are read at call time (port.ts → server.ts → http.ts load order).
- `apps/lab/lib/services/pipelines/port.ts`: `labPipelines(env) ?? UNAVAILABLE`.
- `apps/lab/tests/p/{http.test.ts,wiring.test.ts,run-mutants.mjs,backend.py,stack.test.ts}`.

## Failing seam first
`node --test tests/p/http.test.ts tests/p/wiring.test.ts` on the base code → exit 1, 6 tests, 2 pass, 4 fail (H01, H03, H04, W01). After: 6/6.

## Checks (at a408d8ff)
| command | exit | result |
|---|---|---|
| `node --test tests/p/*.test.ts` | 0 | 36 tests, 35 pass, 1 skipped (stack) |
| `node tests/p/run-mutants.mjs` (in `make lab-mutants`) | 0 | 35 cases all named; 150/150 killed (32 new: P4-X119..X150 + re-anchored X04/X99/X107/X110/X111) |
| `LAB_P4_REAL=1 INFRX_D_TASK=p1 node --test tests/p/stack.test.ts` | 0 | 6 pass (S01..S05 = J01..J05) |
| `make lab-test` / `lab-lint` / `lab-typecheck` / `lab-build` / `lab-mutants` | 0 | see B4-swap-a408d8f.md (238/229/0/9; clean; 12 runners 0 survivors) |

Swap cases named by mutants: an unreadable row (H03: X123..X150), a 5xx (H02: X117), a missing env (W02: X04), no token (H04: X122), env name / session token (W01: X119..X121).

## Real route (backend.py)
`lab_pipelines.register` over the REAL D7 `PgLabDataStore` (an N1-imported 8-sample dataset; annotation and external-run records; checkpoint receipts in `infrx.lab_checkpoint_receipts`) and the REAL L2 `LabAccess(PgAccessStore)` (roles, P1 reviewer checks, P3's grant check, the database clock). D8's label log / run ledger and B3's evaluation port are the route suite's fakes (SR-P1-1, SR-P3-1); the ledger's checkpoint listing (WR-LAB2-4) reads the real D7 receipts. Doors: `/_test/artifact` (provider upload), `/_test/evaluated` (B3 result), `/_test/ambiguous`, `/_test/revoke` (lab-sql's real `lab_revoke_access_grant`), `/_test/advance` (`infrx_test.clock`). The backend prints each refusal's cause on stderr.
- S01 (J01): 2 accepted / rows 3,4,5 rejected (forged ground truth, unknown sample, bad method); replay = stored receipt; other rows 409; synthetic vs imported vs human stay apart and only the human review is ground truth; adjudication; the train-only export's lineage keeps methods apart (`["human","imported"]`, `["human"]`, `["imported"]`) and omits the held-out label with its reason; export replay equal (key order differs: compared structurally), other adapter 409.
- S02 (J02): manual bundle prepared (`reserved 0.00000000`, unsettled, cost null), replay equal, other limit 409; submit ×2, finish ×2; approve before a checkpoint 409; checkpoint validated + queued on the frozen holdout, redelivery equal; approve before B3 succeeds 409, after → eligible.
- S03 (J03): digest mismatch and missing artifact rejected with their reasons, never evaluated; a key outside the run's prefix 422; approving a rejected one 409.
- S04 (J04): a lost submission reads `ambiguous` from the ledger and the manual connector's lookup resolves it to `submitted` (the manual connector cannot show "never resubmitted"; that half is P3's own suite).
- S05 (J05): OTHER's developer 404 on A (and empty own lists); viewer and consumer denied; a developer assigning 403; a foreign payer 422; an expired export (database clock +61 s past a 60 s TTL) 410 gone; then C1 revokes its grant through the real RPC and a submit is denied, the run stays prepared.

## Wiring request
WR-LAB-UI-SWAP-1 (all three env names; diff and proof in `research/plan/evidence/b/B4-swap-a408d8f.md`).

## Estimate (remaining)
optimistic 0.5 h, likely 1 h, pessimistic 2 h; confidence medium; basis: L4 swap analogue 1/2/4; remaining = verify round, env wiring, rerun when D8's label log/run ledger (SR-P1-1/SR-P3-1) merge.
