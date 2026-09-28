# R4 swap (WR-R4-1, adapter half) — lane lab-ui-swap, LW5

- Branch `codex/w5-lab-ui-swap`, base `ae72957f`, task commit `a408d8ff` = head (checks at the head). Key `r2` (57534) only, `INFRX_D_TASK=r2`; no container left.

## Changed paths (owned)
- `apps/lab/lib/services/rollouts/server.ts` (new): `labReleases(env)` — `LAB_RELEASES_API_URL` server-only, unset = `null`; token = `sessionToken(config)`.
- `apps/lab/lib/services/rollouts/http.ts`: per-call token (none: nothing sent); every renamed record checked — Release (state, fence, plan + budget Amount, candidates, progress arms with `p99Ms` null-or-number, assignments, verdict action), Decision, Proposal, Variant (identities, comparison outcome, performance, claim flag). One unreadable record fails the whole answer closed. Refusals unchanged (no `gone` in the port: 410 is unavailable).
- `apps/lab/lib/services/rollouts/port.ts`: `labReleases(env) ?? UNAVAILABLE`.
- `apps/lab/tests/r/{http.test.ts,wiring.test.ts,run-mutants.mjs,backend.py,stack.test.ts}` (H01 now on the full fixtures, snake_cased by the test itself).

## Failing seam first
`node --test tests/r/http.test.ts tests/r/wiring.test.ts` on the base code → exit 1, 6 tests, 2 pass, 4 fail (H01, H03, H04, W01). After: 6/6.

## Checks (at a408d8ff)
| command | exit | result |
|---|---|---|
| `node --test tests/r/*.test.ts` | 0 | 29 tests, 28 pass, 1 skipped (stack) |
| `node tests/r/run-mutants.mjs` (in `make lab-mutants`) | 0 | 28 cases all named; 118/118 killed (27 new: R4-X92..X118 + re-anchored X01/X78/X84) |
| `LAB_R4_REAL=1 INFRX_D_TASK=r2 node --test tests/r/stack.test.ts` | 0 | 5 pass (S01..S04 = J01..J04) |
| `make lab-test` / `lab-lint` / `lab-typecheck` / `lab-build` / `lab-mutants` | 0 | see B4-swap-a408d8f.md |

Swap cases named by mutants: an unreadable row (H03: X96..X118), a 5xx (H02: X90), a missing env (W02: X01), no token (H04: X95), env name / session token (W01: X92..X94).

## Real route (backend.py)
`lab_releases.register` over the REAL D9 (`PgReleaseStore`, 0033/0039 — merged on this base, so D9 is real here where `test_lab_releases_pg.py` still used R2's fake) with R2's own `Controller` deciding on it, and the REAL L2 `LabAccess(PgAccessStore)`. Each canary revision and its two B1 runs (on a D7-published dataset/harness/evaluator) are published through D7, so D9's "an expansion names the provider's evaluation runs" check passes for real. The read models (WR-R4-1's lab-sql half) are the backend's own listing over the real rows: D9's release row + `lab_rollout_events` decisions, R1 progress / R2 verdict as last seen; the proposal store (WR-R4-2) is the route suite's fake; L3's alias is R2's `FakeServing`.
- S01 (J01): a breach → one D9 rollback by the controller (fence 1→2); the next pass returns R2's settled-state `rolled_back` and only converges; the page's lineage/status come from the records; a later accepting report leaves it rolled back.
- S02 (J02): accepting report → expand verdict; the administrator's proposal changes nothing; the operator's R2 approval moves D9 to approved with the two B1 runs as evidence; a rollback proposal + the operator's emergency rollback → rolled_back.
- S03 (J03): OTHER's user 404 (and an empty own listing); viewer/developer 403; consumer denied; viewer reads; stale fence 409; inconclusive verdict never expands 409; double click 409 (one proposal); a rolled-back release refuses both kinds.
- S04 (J04): an expansion approved after a breach rolled the release back is refused by D9's CAS (the proposal rejected, one decision); an operator rejection leaves the release running at fence 1 with no decision; `/lab/v1/optimizations` reads through the same adapter.

## Finding for WR-R4-1's lab-sql half (proposed ruling text)
R2's `Controller.step` on a settled release returns a pseudo-verdict whose `action` is the release state (`rolled_back`, `approved`). The Lab's `Verdict.action` is `rollback | hold | expand`, so a read model that stores every `step` result as "the latest verdict" makes the Lab's row check fail the whole releases answer closed. Proposed: "The releases read model's `verdict` is R2's latest `evaluate` verdict (action rollback|hold|expand); a settled-state answer of `step` is never stored as a verdict." The backend here follows that.

## Wiring request
WR-LAB-UI-SWAP-1 (all three env names; diff and proof in `research/plan/evidence/b/B4-swap-a408d8f.md`).

## Estimate (remaining)
optimistic 0.5 h, likely 1 h, pessimistic 2 h; confidence medium; basis: L4 swap analogue 1/2/4; remaining = verify round, env wiring, rerun on lab-sql's read models + proposal store (WR-R4-1/WR-R4-2).
