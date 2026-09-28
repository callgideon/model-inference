# B4 swap (WR-B4-1, adapter half) — lane lab-ui-swap, LW5

- Branch `codex/w5-lab-ui-swap`, worktree `.claude/worktrees/codex-w5-lab-ui-swap`, base `ae72957f`, task commit `9a522f1c`, head `a408d8ff` (all checks below ran at the head).
- Keys: `b3` (57522) only, `INFRX_D_TASK=b3` for every PG run; containers created and removed by `pgharness` (none left). `lab-v1m` was not needed (the traces code is untouched).

## Changed paths (owned)
- `apps/lab/lib/services/evaluation/server.ts` (new): `labEvaluation(env)` — `LAB_EVALS_API_URL` server-only, unset or no Supabase config = `null`; `sessionToken(config)` reads the Lab session cookie's own access token (V1M's pattern, loaded on use), shared with P4/R4. No service key anywhere.
- `apps/lab/lib/services/evaluation/shape.ts` (new): the row checks (`obj/list/map/oneOf/nul/opt/str/num/bool`), shared with P4/R4.
- `apps/lab/lib/services/evaluation/http.ts`: `token` is a per-call getter (none: nothing sent, `unavailable`); every answer checked against the port's record shapes (Run, Experiment + B2 Report, Catalog incl. H1 harness fields, Subscription + B3 decisions); one unreadable row fails the whole answer closed. Refusals unchanged: 401/403 denied, 404 not_found, 409 conflict, 422 invalid, anything else (410/5xx/transport/unparseable) unavailable (the port has no `gone`).
- `apps/lab/lib/services/evaluation/port.ts`: `UNAVAILABLE` → `labEvaluation(env) ?? UNAVAILABLE` (preview stays first, dev only). The actor is still the guarded workspace (actions/pages unchanged).
- `apps/lab/tests/b/{http.test.ts,wiring.test.ts,run-mutants.mjs,backend.py,stack.test.ts}`.

## Failing seam first (recorded before the implementation)
`node --test tests/b/http.test.ts tests/b/wiring.test.ts` on the base code → exit 1, 6 tests, 2 pass, 4 fail: H01 (token getter), H03 (unreadable rows reached the page), H04 (sent with no token), W01 (port ignored `LAB_EVALS_API_URL`). After: 6/6.

## Checks (at a408d8ff)
| command | exit | result |
|---|---|---|
| `node --test tests/b/*.test.ts` | 0 | 34 tests, 33 pass, 1 skipped (stack) |
| `node tests/b/run-mutants.mjs` (in `make lab-mutants`) | 0 | 33 cases all named; 151/151 killed (28 new: B4-X125..X151 + re-anchored X44/X115/X117) |
| `LAB_B4_REAL=1 INFRX_D_TASK=b3 node --test tests/b/stack.test.ts` | 0 | 4 pass (S01..S03 = J01..J03 on the real route) |
| `make lab-test` | 0 | 238 tests, 229 pass, 0 fail, 9 skipped (base 223) |
| `make lab-lint` / `make lab-typecheck` / `make lab-build` | 0 / 0 / 0 | clean |
| `make lab-mutants` | 0 | 12 runners, 0 survivors (b 151, p 150, r 118, others unchanged) |
| `uv run --frozen pytest -q tests/i/lab/test_lab_packaging.py` with WR-LAB-UI-SWAP-1 applied | 0 | 11 passed (then reverted; see wiring) |

Swap cases named by mutants: an unreadable row (H03: X131..X151), a 5xx (H02: X123), a missing env (W02: X44), no session token (H04: X130), the env name and the session token (W01: X125..X129).

## Real route (backend.py)
`lab_evaluations.register` as merged over the REAL D7 `PgLabDataStore` (dataset, harness, evaluator via 0034 `put_evaluator`, an external run record; runs created by B1's `freeze`) and the REAL L2 `LabAccess(PgAccessStore)` (d7.seed world: NEMO dev/viewer, OTHER's developer, a consumer-only user). Experiments (WR-B4-2), catalog (WR-LAB2-2) and the B3 ledger listing (WR-B3-1) are the route suite's fakes: 0042/0043 are not on this base. Test-only doors: `/_test/state` (D7's own state trigger decides), `/_test/settle` (B2's stored report), `/_test/decide` (a B3 decision).
- S01 (J01): launch → two queued D7 runs (resubmit = same), running → cancel → 409 again; succeeded → 409; the stored report reads through the row check. 
- S02 (J02): OTHER's developer sees empty lists, 404 on A's run and on A's reads (lab_auth: a non-member's provider is not found — the fake said denied; the route is the oracle); consumer-only denied; viewer reads, cannot launch; foreign dataset/unknown serving 422; same id other body 409; malformed id 422; still two runs.
- S03 (J03): subscribe once (replay equal), other body 409, viewer 403, run_limit > limit 422, PROVIDER_USD 422, foreign external run 422; a B3 decision reads as "evaluation queued · run r1".

## Wiring request (coordinator-owned files)
WR-LAB-UI-SWAP-1 (proposed id): declare `LAB_EVALS_API_URL`, `LAB_PIPELINES_API_URL`, `LAB_RELEASES_API_URL` in `infra/lab/app/lab.json` `env.lab-web` + `infra/lab/app/README.md` §2 + `apps/lab/.env.example`. Exact diff: `research/plan/evidence/b/WR-LAB-UI-SWAP-1-a408d8f.patch` (`git apply` from the repo root). Proof: all three applied → `tests/i/lab/test_lab_packaging.py` 11 passed; `.env.example` alone → `test_i2l__secret_names_only_and_every_name_the_files_read_is_declared` FAILS at `read <= set(declared)` (:152); manifest + `.env.example` without the runbook rows → the same test FAILS at the runbook assertion (:155). Without any of it nothing fails, but the three names are settings no manifest reviews.

## Open issues
- The real experiments/catalog/ledger tables (0042/0043, batch #16) are not on this base: rerun `stack.test.ts` with their adapters once merged (the backend swaps the three fakes for them; nothing in the Lab changes).
- Composition: lab-api still mounts `/lab/v1/evaluations` only with `LAB_EVALS` on (off); the Lab side is off until `LAB_EVALS_API_URL` is set.

## Estimate (remaining for B4 swap)
optimistic 0.5 h, likely 1 h, pessimistic 3 h; confidence medium; basis: the L4 swap analogue 1/2/4 per adapter, done in this session — what remains is one verify round, the env wiring, and a rerun on 0042/0043.
