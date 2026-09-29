# LAB-DEPLOY-PREP (LW6, task I2L follow-up): `make lab-local` (E4-ON), the Lab internal-testing runbook, the Lab box rollout steps

- Lane: lab-deploy-prep; branch `codex/w5-lab-deploy-prep`; worktree `.claude/worktrees/codex-w5-lab-deploy-prep`.
- Base `ed071536`; head under test `24512389` (commits `266f03cc` step 1, `c70270f2` step 2,
  `bdecfcb9` step 3, `24512389` step 1b; the evidence commit follows).
- Nothing touched the pilot box, AWS/SSM/S3, Vercel, hosted Supabase or a secret. Docker only
  on this lane's key (`infrx-lab-on-*`, 57537-57539) and the borrowed e3l block under E3L's
  runner lock (`/tmp/infrx-e3l.runner.lock`; torn down by the run). Foreign leftovers
  (`infrx-d1-postgres`, `infrx-d2-*`, `infrx-t2f-*`, `infrx-q3-valkey`, `infrx-m5-s3`,
  `infrx-e8l-postgres`, the `infrx-e5l_*` volumes, `infrx-b3-postgres` when present) were not
  touched: recorded, left as found.

## Changed paths

| Step | Paths |
|---|---|
| 1 `make lab-local` | `tests/integration/lab-local.sh`, `tests/integration/lab_local/{runner,lab_world,scenarios_on,conftest,mutants,test_mutants,test_lab_local_runner}.py`, `Makefile` (`lab-local` + `.PHONY`), `apps/infrx-api/infrx/contracts/tasklocal.py` (the one key `lab-on`: postgres 57537, valkey 57538, valkey-q 57539) |
| 2 runbook | `research/plan/consumer-v1/08-lab-internal-testing-rollout.md`, `research/plan/consumer-v1/README.md` (index line + audit) |
| 3 box steps | `infra/lab/rollout/{lib.sh,lab-migrate.sh}`, `infra/lab/rollout/steps/{10-lab-preflight,20-lab-image,30-lab-units,40-lab-control,45-lab-site,50-lab-role,60-lab-smoke,90-lab-revert}.sh`, `apps/infrx-api/tests/i/lab/test_lab_rollout_steps.py`, `apps/infrx-api/tests/i/lab/mutants.py` (+32 mutants, a second runner) |
| evidence | `research/plan/evidence/e/E4ON-raw-24512389/`, this file, `research/plan/evidence/coordinator/updates/I2L-20260929T0950Z.json` |

## 1. The E4-ON verdict (`make lab-local` at 24512389, exit 1 = FAIL)

`GATE_ARGS=--reuse make lab-local` → `research/plan/evidence/e/E4ON-raw-24512389/verdict.json`
(1,436 s E4 stage; the whole run ≈ 26 min on the kept e3l stack, then torn down).

| Stage | Verdict | Detail |
|---|---|---|
| stack | usable | E2 services in e3l, migrations 0001–0051 applied (run.py migrate) |
| lab-build | PASS | `pnpm build`, the composition's public names inlined |
| e4-on | **FAIL** | `tests/g tests/w tests/contracts tests/i/test_packaging.py` with every switch ON, D harness on `lab-on`, MinIO the stack's: **2,829 tests: 2,784 passed, 36 failed, 9 skipped** |
| scenarios | **FAIL** | o01 PASS, o02 PASS, o03 NOT RUN, o04 PASS, o05 FAIL, o06 PASS, o07 NOT RUN (below) |
| journey:datasets | PASS | `LAB_N_REAL=1 INFRX_D_TASK=lab-on node --test tests/n/journey.test.ts` (the Lab's N4 journey on the real N1/N2) |
| journey:releases / pipelines / evaluations / traces | NOT RUN | key-pinned backends (r2, p1, b3, lab-v1m): WR-LDP-1; rerun commands in the verdict |

**E4-ON stage, the 36 failures, classified:**
- 35 are the two pins of the new tasklocal key (not product behaviour): `tests/contracts/lab/test_lab_contracts.py::test_lab_existing_frozen_contracts_are_byte_identical` (the frozen sha of `infrx/contracts/tasklocal.py`) and `tests/contracts/test_config_and_imports.py::test_every_lab_lane_port_sits_in_one_band` (`LAB_LANE_PORTS` lacks `lab-on`), plus 33 mutant-runner cases - `tests/contracts/test_mutants.py` 25 (22 mutants + 3 runner self-tests) and `tests/contracts/lab/test_mutants.py` 8 - each "broken_runner: pristine baseline: the unmutated tree fails … test_every_lab_lane_port_sits_in_one_band / test_lab_existing_frozen_contracts_are_byte_identical". WR-LDP-5 (two pin lines) removes all 35; nothing else in those lists failed.
- 1 is the switch itself: `tests/w/test_worker_main.py::test_worker_main_pg__the_pilot_box_starts_the_real_worker_and_waits_for_it` — its worker inherits `LAB_EVAL_WORKER=true` and refuses by name ("LAB_EVAL_WORKER needs an evaluator source (WR-B-2(b)) and a dev target source (WR-B-3)"), exactly R198's seam (o02 proves the same refusal on purpose). **LDP-F4**: the consumer worker cannot run with `LAB_EVAL_WORKER` ON on this base, by design; the E4 subset is green with it OFF (the composition lanes' E4, 2800/0).
- The 9 skips are other lanes' key-scoped PG/ClickHouse cases (b1/b3/p1/p2/r2 keys, the t2i ClickHouse stack, the t2f proof): pre-existing, as in COMPOSITION-3's E4 row.

**Scenarios (one composition, every switch ON):**

| Id | Status | What it showed |
|---|---|---|
| o01 | PASS | the gateway starts ready with all ten gateway switches ON on 0021's `infrx_runtime` login (ROLLOUT_ROUTING refuses any other) |
| o02 | PASS | the consumer worker starts with `TRACE_PUMPS` ON (spool, ClickHouse, trace bucket); with `LAB_EVAL_WORKER` ON it refuses by name, exit 2 (R198) |
| o03 | NOT RUN | `eval`, `judge`, `datasets` start ready on their own env; `checkpoints` (WR-B3-3), `annotation` (WR-P2-4, composition-4), `training` (P-11: no worker pass by design), `rollout` (WR-LSQ-9) refuse by name, exit 2 (R198/R211) |
| o04 | PASS | the Lab control factory (`infrx.lab.control.app`) ready; `GET /lab/v1/control/deployments` 200 for A's developer, B's developer refused |
| o05 | **FAIL** | all-switches gateway: every Lab family 503 `unavailable` and datasets **500** (LDP-F1, LDP-F3); the Lab-routes gateway (owner login, ROLLOUT_ROUTING off, every other switch ON): control, traces, datasets 200, the checkpoint receiver mounted (unsigned post refused); evals, pipelines, teacher-batches, releases, optimizations typed 503 until WR-B4-2 / WR-LAB2-4 / WR-P4B-1 / WR-R4-1/2 (NOT RUN); a consumer key refused on every family (PASS) |
| o06 | PASS | with every switch ON (ROLLOUT_ROUTING's routed accept on the path) a sync and an async text request serve; the async one succeeds, one inference attempt, settled once, CREDIT books conserved |
| o07 | NOT RUN | the Lab web (production build, `https://localhost:57061` via the pinned Caddy `tls internal`) signed in with a real `sb-infrx-lab-auth` session: `/requests`, `/annotations`, `/judge`, `/datasets`, `/settings` render signed in with no alert; `/overview`, `/models`, `/deployments` "unavailable" (WR-E3L-J: no HTTP control adapter), `/evaluations*`, `/training`, `/releases`, `/optimizations` "unavailable" (their pending lanes); signed out, no page is signed in |

**Findings (filed, none patched here):**
- **LDP-F1** (lab-sql + composition): every switch ON forces the gateway onto `infrx_runtime` (ROLLOUT_ROUTING, SR-R1-1), which holds no EXECUTE on L2's `infrx.lab_provider_memberships` (and the other `infrx.lab_*` RPCs): `psycopg.errors.InsufficientPrivilege: permission denied for function lab_provider_memberships` on every Lab route (gateway log `cases/composition/gateway-1.log`). The all-switches gateway cannot serve the Lab. Fix: grant L2's read RPCs to `infrx_runtime`, or serve the Lab only from the control unit (WR-LDP-2).
- **LDP-F2** = WR-E3L-J (lab-app-control lane, running): `apps/lab/lib/services/control/port.ts` `controlPort()` returns `UNAVAILABLE` in production.
- **LDP-F3** (G/datasets owner): `/lab/v1/providers/{p}/datasets/*` lets a store exception escape as a 500 (`Exception in ASGI application`), where every other Lab family answers `lab_auth.guarded`'s typed 503.
- **LDP-F4** (by design, R198): `LAB_EVAL_WORKER` ON in a consumer worker refuses to start; the E4 subset's pilot-box worker case inherits it (above).
- **LDP-F5** (I2L owner, WR-LDP-3): `infra/lab/app/lab.json` `lab-workers` omits names the entry point reads (`LAB_S3_PREFIX`, `LAB_EVAL_ENDPOINT_URL`, `LAB_EVAL_ENDPOINT_KEY`, the `*_CONCURRENCY` of eval/checkpoints/datasets).
- **LDP-F6** (I/rollout, WR-LDP-4): `infra/rollout/hosted-migrate.sh` is built for 0019–0026 with the App in maintenance (EXPECTED_PENDING, the 0018/0026 end checks, the W7 `/health` 503 precondition): the Lab window needs its reviewed patch (runbook §2).

Fail-first: on the base, `local_services("lab-on") == {}` (a fake-only `l` key: no PostgreSQL for the E4-ON stage) and `make lab-local` is "No rule to make target"; the first scenario runs failed on real composition gaps (o02 the seam, o03 the pending roles, o05, o07) before the NOT RUN vocabulary named them; o07's first oracle (signed-out = 404) failed against the real Lab web (it renders the sign-in form, 200) and became "no `Sign out` form".

## 2. The runbook

`research/plan/consumer-v1/08-lab-internal-testing-rollout.md`: §0 what internal testing covers
on this base (from E4-ON), §1 go/no-go, §2 R151's three conditions with commands (the
`schema_proof.py` re-proof + `known-good.py --list --applied 0051`, the reviewed
`EXPECTED_PENDING` diff, the window record) and `lab-migrate.sh`, §3 SSM parameter names, §4 the
box steps via `infra/rollout/ssm.sh` with each proof, §5 switch order and rules (the App
gateway's switches stay OFF), §6 the `infrx-lab` Vercel project (env names, domain, Supabase
Redirect URLs, the App's `REDIRECT_ALLOWLIST.production`), §7 operator/membership onboarding SQL,
§8 the tester checklist and feedback path, §9 rollback (switches first, migrations not reverted),
§10 wiring requests, §11 the proof table.

## 3. The box steps and the R151 gate (tested against a box stand-in)

| Command | Exit | Counts |
|---|---|---|
| `cd apps/infrx-api && uv run --frozen pytest -q tests/i/lab` | 0 | 34 passed (I2L 17 + LAB-DEPLOY-PREP 15 + mutant list checks) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab/test_mutants.py` | 0 | 60 passed: 56 mutants killed (I2L 24 + 32 new), 0 survivors |
| failing first | — | `test_ldp__the_image_is_built_once…` failed against the first `20-lab-image.sh` (a failed `docker image inspect`'s stdout taken as an id), fixed; `image_rebuilt_every_time`/`image_not_recorded` died by exceptions (broken_runner) until the case asserted, then killed; `test_ldp__todays_hosted_migrate_is_refused_for_the_lab_apply` proves the tree as it stands is refused (condition 2) |

## Checks (all at 24512389 unless noted)

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/lab_local/test_lab_local_runner.py tests/integration/lab_local/test_mutants.py` | 0 | 19 passed, 1 skipped (the empty default stack parametrization) |
| 2 | `INFRX_MUTANTS=all … tests/integration/lab_local/test_mutants.py -k "not stack"` | 0 | 22 passed: 19 layer-1 mutants killed + list checks |
| 3 | `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e3l … test_mutants.py -k stack` (kept e3l stack) | 0 | 10 passed: 9 stack mutants killed (pristine baseline green; KNOWN_FAIL = o05 all-switches) |
| 4 | `pytest -q tests/integration/lab_{operate,evaluate,observe,rollout,improve,local} tests/integration/test_lab_package_isolation.py` | 0 | 125 passed, 6 skipped (the sibling gates' own empty/stack skips) — no package collision |
| 5 | `ruff check tests/integration/lab_local apps/infrx-api/tests/i/lab apps/infrx-api/infrx/contracts/tasklocal.py` | 0 | clean |
| 6 | `make lab-lint` / `make lab-typecheck` / `make lab-build` / `make lab-test` | 0 / 0 / 0 / 0 | lab-test 248 tests: 239 pass, 9 skipped (the Lab's own real-stack cases), 0 fail |
| 7 | `make lab-local` | 1 | FAIL (§1), `E4ON-raw-24512389/verdict.json` |
| 8 | `INFRX_D_TASK=lab-on INFRX_D2_VALKEY_PORT=57538 INFRX_D2_VALKEY_CONTAINER=infrx-lab-on-valkey INFRX_Q_VALKEY_PORT=57539 make api-test` | 2 | **6,637 passed, 35 failed, 167 skipped, 9 xfailed** (1 h 28 min). All 35 failures are WR-LDP-5: the two tasklocal pins (`test_lab_existing_frozen_contracts_are_byte_identical`, `test_every_lab_lane_port_sits_in_one_band`) and 33 mutant-runner cases whose pristine baseline fails on them (`tests/contracts/test_mutants.py` 25, `tests/contracts/lab/test_mutants.py` 8); nothing else failed (JUnit classified). The skips are the other keys' PG/ClickHouse/MinIO cases and empty mutant parameter sets, as on every key but theirs. A first attempt was killed by an external SIGTERM at 34 % (make Error 143) and rerun whole |

## Wiring requests

- **WR-LDP-5** (coordinator, contracts pins; blocks a green `make api-test`): the new key's two pins —
  `apps/infrx-api/tests/contracts/lab/frozen_contracts.json`: `"apps/infrx-api/infrx/contracts/tasklocal.py": "0624c8eb3b25dbdd163cf3160f7b3d3b5ce6f503c8cee42fa4618e6c347cf297"`;
  `apps/infrx-api/tests/contracts/test_config_and_imports.py` `LAB_LANE_PORTS` += `"lab-on": {"postgres": 57537, "valkey": 57538, "valkey-q": 57539}`.
  Test: `uv run --frozen pytest -q tests/contracts` green, then the E4-ON stage's 34 pin failures are gone.
- **WR-LDP-1** (Lab app lanes): `apps/lab/tests/{r,p,b,v/list}/backend.py` accept `INFRX_D_TASK=lab-on` (each `!= "<key>"` guard → `not in ("<key>", "lab-on")`), so `make lab-local` runs those journeys on its own key.
- **WR-LDP-2** (composition lane): mount `lab_datasets`, `lab_evaluations`, `lab_pipelines`, `lab_releases` (and `lab_checkpoints`) in `infrx.lab.control.app` from `pilot._lab`/`_lab_2`/`_lab_checkpoints` on `INFRX_LAB_DATABASE_URL`, so the box serves every Lab family from the control unit and the App gateway keeps every Lab switch OFF. Test: E4-ON o05 on the control factory.
- **WR-LDP-3** (I2L owner): declare in `infra/lab/app/lab.json` the worker names of LDP-F5.
- **WR-LDP-4** (I/rollout lane): the reviewed `hosted-migrate.sh` patch of runbook §2 condition 2 and the W7 maintenance precondition for an additive Lab window.
- **WR-LDP-6** (coordinator, optional): a dedicated `harness.NAMESPACES` block for `lab-on` (today E3L's e3l block is borrowed under E3L's own runner lock; no 100-port block is free in 57500–57599).

## Proposed rulings (not numbered)

- **E4-ON is the gate before any hosted enable of a Lab switch**: `make lab-local` composes every switch ON; a Lab switch may be turned on in a hosted deployment only when its families are PASS (not NOT RUN) in the latest E4-ON verdict at that release, and the App's o06 is PASS.
- **The box serves the Lab from its own units only**: on the pilot box the App gateway's Lab switches stay OFF; the Lab's routes are served by the control unit (WR-LDP-2), so a Lab fault can never change the App's gateway (extends WR-I2L-2b).

## Open issues

- `make api-test` on this branch fails exactly the WR-LDP-5 pins (35 cases incl. mutant cascades) until the wiring lands (row 8); every other case is green on `lab-on`.
- The borrowed e3l block: `make lab-local` BLOCKs while E3L's own runner (or a kept E3L stack) holds it.

## Estimate (remaining for this lane)

optimistic 0.5 h / likely 1.5 h / pessimistic 4 h, confidence medium. Basis: all three steps
done with 0 survivors; remaining = one verify round and the WR-LDP-5 re-run of `make api-test`
(~45 min) and of `make lab-local` (~26 min). The operator's hosted window itself (runbook §2–§8)
is P-08-gated and not in this estimate: ~3–5 h once P-08 and WR-LDP-2/4/5 land (analogue: the
I2B window).
