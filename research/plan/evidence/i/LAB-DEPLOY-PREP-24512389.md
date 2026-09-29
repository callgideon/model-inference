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

## Fix round (2026-09-29, handback e65db427 -> code head 4aed97ac)

Base for this round: `e65db427` plus a merge of the `claude/consumer-v1` tip `72f1a74a`
(`1dbe0d9e`, the only way to fix LDP-RSI-1 without a rebase; its 2 textual conflicts were
resolved by union: the Makefile `.PHONY` line and the consumer-v1 README audit log). Commits:
`1dbe0d9e` merge, `0926afdb` + `82a81c35` + `28c9c2cc` lab_local, `950a570b` steps + runbook,
`b03a6814` + `4aed97ac` runbook/comments. `make lab-local` verdict at `28c9c2cc`
(`4aed97ac` changes comments and the runbook only). Nothing touched the box, AWS/SSM/S3,
Vercel, hosted Supabase or a secret; docker only on lab-on and the borrowed e3l block under its
lock (torn down; no `infrx-e3l-*` / `infrx-e3llab-tls` container remains). Foreign leftovers
(`infrx-m5-s3`, `infrx-q3-valkey`, `gideon-migration-order-test-*`) left as found.

| Finding | Fixed | What changed (tests first) |
|---|---|---|
| 0-LDP-R1 | yes | `lab-migrate.sh` checks the W7 post-check line itself, `*"NNNN name"$'\n'"nothing pending")` (migrate.py plan's space form). The gate stub now carries a real `case "$POST"` line; new R151 sub-case: EXPECTED_PENDING matches but the post-check names 0026 -> refused. `test_ldp__todays_hosted_migrate_carries_the_reviewed_patch`: the tip's real `hosted-migrate.sh` (bcb73cc1 carries the reviewed patch) passes condition 2 and the gate stops at condition 1 (a refusing KNOWN_GOOD). Before the fix it stopped at condition 2 (the reported defect). The real `known-good.py --list --applied 0051` exits 0 at the tip (aafd387e, manual check). Mutants `r151_no_post_check` and `r151_post_check_file_name` are killed. |
| 0-LDP-R2 | yes | (1) `test_ldp__every_secret_is_refused_as_a_literal` checks all 5 names in `secrets` (the DSN with its password, both tokens, the endpoint key, CLICKHOUSE_URL) against a list held in the test. There is one mutant per name (`secret_literal_*`), all killed. (2) `journey_status(code, text)` is PASS only when `# tests N` > 0 and `# pass` == N. `test_lab_local_a_journey_passes_only_when_every_case_ran_and_passed` drives `journeys()` through a stubbed `node`: exit 1 -> FAIL; skipped / `# tests 0` / no summary -> BLOCKED. Mutants `journey_always_passes`, `journey_skips_pass` and `journey_empty_passes` are killed. (3) = R1. |
| 0-LDP-R3 | yes | `50-lab-role.sh` has per-role `needs=` (= `infrx.lab.workers` NEEDS; AGREE now asserts equality). A SPEC missing one is exit 2 before any change (`test_ldp__a_spec_without_a_name_the_role_needs_is_refused_before_any_change`: judge/datasets without CLICKHOUSE_URL or S3_TRACE_BUCKET, etc.). Exit 5 is reserved for `pending=" checkpoints training rollout "`. A served role's exit 2 is exit 4, "refused its settings". Mutants `role_needs_unchecked`, `role_needs_drift` and `served_refusal_is_a_pending_lane` are killed. Runbook §0/§3/§4 L7/§5: this box runs `eval` only; judge and datasets wait for CLICKHOUSE_URL + S3_TRACE_BUCKET (the trace projection). |
| 0-LDP-R4 | yes (as asked: proven where possible, NOT RUN where not) | The composition starts the control factory on 0043's `infrx_lab_control`, with a local password set as the operator would, first and alone. Case `test_o04_the_control_factory_is_ready_on_its_own_login` reads it; layer-1 `test_lab_local_the_control_factory_runs_on_its_own_login_never_the_owner` has mutant `control_on_the_owner`. The owner-login factory still serves o04's session case and o05, so those families are judged apart from the new finding LDP-F7. **LDP-F7 (new, real)**: `infrx.lab.control.app` `_store()`/`_compose()` build `connector(dsn)` without `set_role=False`. Off the :6543 pooler it runs `set role service_role`, which `infrx_lab_control` (a member of no role) is refused, so `/readyz` 503 (reproduced by hand: `permission denied to set role "service_role"`). It is KNOWN_FAIL in the stack list. o03 is recorded as proven on the owner login only. WR-LDP-7 asks for per-role logins, and the runbook's L5/L7 proofs depend on LDP-F7/WR-LDP-7. |
| 1-LDP-RSI-1 | yes | LAB_TEACHERS moved into GATEWAY_SWITCHES, PENDING_SWITCHES = {}. LAB_TEACHER_URL = the teacher fake (switch env and role env). LAB_CONTROL_URL = the control factory for the Lab web (the o07 control pages now render: **LDP-F2 resolved**, `/overview` `/models` `/deployments` are absent from o07-pages.json). `annotation` is no longer pending (its teacher-collect pass is on the tip; o03 starts it). 50-lab-role.sh allows and needs LAB_TEACHER_URL for annotation. Before the fix, after the merge: lab_local 4 failed, tests/i/lab 1 failed (as reported). |
| 1-LDP-RSI-2 | no (coordinator's WR-LDP-5, unchanged) | tasklocal.py sha at this head = `0624c8eb3b25dbdd163cf3160f7b3d3b5ce6f503c8cee42fa4618e6c347cf297` (the WR-LDP-5 value). 57537-57539 are free at the tip. `infrx/contracts/tasklocal.py` is the file that holds the key (`tests/d/tasklocal.py` does not exist). |

Also fixed during the round: the E4-ON stage now passes `INFRX_M_S3_BUCKET` (the stack's media
bucket). Without it, the tip's `tests/w/test_lab_workers_lineage_pg.py` fell back to `infrx-n1`,
which does not exist on the e3l MinIO, and failed "the media object store did not answer" in
the 950a570b run.

### E4-ON at 28c9c2cc (`GATE_ARGS=--reuse make lab-local`, exit 1 = FAIL): `E4ON-raw-28c9c2cc/`

- lab-build PASS; journey:datasets PASS; 4 key-pinned journeys NOT RUN (WR-LDP-1).
- e4-on FAIL: 2794 passed / 36 failed / 14 skipped (other keys). The 36 failures are the 35
  WR-LDP-5 pins and their mutant cascades, plus LDP-F4 (R198 seam, by design). There is nothing new.
- o01 PASS, o02 PASS, o06 PASS.
- o03 NOT RUN: eval, judge, annotation and datasets start (owner login). checkpoints/training/rollout refuse by name.
- o04 FAIL: the owner-login session case PASSes; the `infrx_lab_control` case FAILs (LDP-F7).
- o05 FAIL: LDP-F1 (the all-switches gateway on infrx_runtime: every family 503) and LDP-F3 (datasets 500), unchanged.
- o07 NOT RUN: every page renders signed in except the typed-unavailable evals/pipelines/releases families (WR-B4-2, WR-LAB2-4, WR-R4-1).
- Earlier runs this round: `E4ON-raw-950a570b/` (control on the login inside the composition: o04/o07 FAIL; the n-track bucket failure) is kept. The 82a81c35 run was deleted: its o04 "PASS" was false, because the login factory could not bind the port and the ready probe read the owner-login factory on that port. The case now starts it first and alone.

### Checks (this round)

| Command | Exit | Result |
|---|---|---|
| lab_local layer 1 + tests/i/lab before the fixes, after the merge | 1 | 4 failed / 1 failed (RSI-1 reproduced) |
| the new tests/i/lab cases against the old 50-lab-role.sh / lab-migrate.sh | 1 | 5 failed (fail-first) |
| `pytest tests/integration/lab_local/test_lab_local_runner.py` | 0 | 15 passed |
| `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e3l pytest tests/integration/lab_local/test_mutants.py` (kept stack) | 0 | 38 passed: every layer-1 and stack mutant killed, 0 skipped, 0 survivors |
| `cd apps/infrx-api && pytest -q tests/i/lab` | 0 | 36 passed |
| `INFRX_MUTANTS=all pytest -q tests/i/lab` | 0 | 100 passed (36 cases + the I2L/LDP mutant list), 0 survivors |
| `uv run --frozen ruff check tests/i/lab ../../tests/integration/lab_local` | 0 | clean |
| `make lab-lint`, `make lab-typecheck` | 0 | clean; lab-build PASS inside `make lab-local` |
| `make lab-local` at 28c9c2cc | 1 | FAIL as above (only named findings) |
| `make api-test` | not run whole | its only known reds are the 35 WR-LDP-5 pins (seen inside E4-ON's tests/contracts); rerun after WR-LDP-5 |

### Wiring requests (new/changed)

- **WR-LDP-5** unchanged (sha above).
- **WR-I2L-4b / LDP-F7** (I2L owner): in `infrx/lab/control/app.py`, `_store()` should use `connector(os.environ[DATABASE_URL], set_role=False)` and `_compose()` should use `connector(lab[DATABASE_URL], set_role=False)`. Test: E4-ON o04's login case PASSes; then drop `O04_LOGIN` from `KNOWN_FAIL` in `tests/integration/lab_local/mutants.py`.
- **WR-LDP-7** (lab-sql): one dedicated login per Lab worker role (infrx_lab_eval first), with 0043's shape. `lab_world.role_env` then switches to it, so o03 proves the box's logins.
- The runbook's WR-LDP-4 is marked landed for the EXPECTED_PENDING/post-check half (bcb73cc1). The W7 maintenance precondition half is still open.

### Estimate (remaining)

optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium. Basis: every finding of this
round except RSI-2 (the coordinator's WR-LDP-5) is fixed with 0 survivors. What remains is the
coordinator's merge with WR-LDP-5 plus a rerun of `make api-test` (~45-90 min) and
`make lab-local` (~45 min). LDP-F7 and WR-LDP-7 belong to other lanes (~1-2 h each). The hosted
window stays P-08-gated.

## Coordinator rulings (lab-deploy-prep merge, `codex/w5-merge-47`)

- **R236** (Proposed rulings 1): no hosted Lab switch is turned ON at a release until its route
  family PASSes in the E4-ON composition (`make lab-local`) at that release.
- **R237** (Proposed rulings 2): the pilot box serves the Lab only from its own units (the control
  factory on the `infrx_lab_control` login and the Lab worker roles), with the App gateway's Lab
  switches OFF; the App gateway's Lab families are for the local composition only until WR-LDP-2
  lands.
- Applied in the wirings commit: WR-LDP-5 (tasklocal.py frozen sha `0624c8eb…f297`, unchanged at
  the merge tree; `LAB_LANE_PORTS["lab-on"]`), WR-LDP-1 (the four backend guards accept `lab-on`),
  WR-LDP-3 (`lab-workers` declares LAB_S3_PREFIX, LAB_EVAL_ENDPOINT_URL, LAB_EVAL_ENDPOINT_KEY
  (secret), LAB_EVAL/CHECKPOINTS/DATASETS_CONCURRENCY). WR-LDP-6 skipped: `harness.NAMESPACES`
  is a dict of 100-port blocks and none is free (a block would also need a `TASK_BLOCKS` entry
  in the frozen tasklocal.py); lab-on keeps borrowing e3l under E3L's runner lock.

## Carried

- WR-LDP-2 + LDP-F1 + LDP-F3 → lane lab-control-routes.
- WR-LDP-4 is already on the tip (bcb73cc1/41693d5d: EXPECTED_PENDING 0027–0051 — the next
  reviewed patch is 0027–0052 after merge #46).
- WR-E3L-J landed in merge #38.
- Lens minors LDP-R5/R7, RSI-5 as recorded in the fix round.
