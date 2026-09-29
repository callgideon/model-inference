# E4-ON at fd0aba04 (lane lab-local-2, task I2L / LAB-INTERNAL-TESTING-PREP: WR-LW8-2 + WR-LCR-5's rerun)

- Lane lab-local-2; branch `codex/w5-lab-local-2`; worktree `.claude/worktrees/codex-w5-lab-local-2`;
  base `c77e75ac` (after merge #55: 0056 grants). Commits: `1a5be321` step 1 (R222 machine
  check + o05's control-factory case judged against the owner login), `fd0aba04` step 2a (o07's
  TLS race, clean pin), then this evidence.
- Nothing touched the pilot box, AWS/SSM/S3, Vercel, hosted Supabase, product code, migrations
  or a secret. Docker only on `lab-on` (57537–57539) and the borrowed e3l block under E3L's
  runner lock (`/tmp/infrx-e3l.runner.lock`, free when taken); e5l/e8l/t2f/r2/b1/p3 and other
  lanes' containers were not touched.

## 1. Step 1 (fail-first, `1a5be321`)

- `runner.r222(stages, scenarios)`: `r222.accepted/open/by_design` in verdict.json from
  `OUT_OF_SCOPE` (a product WR per port name: WR-B3-3, WR-LSQ-9, WR-B4-2, WR-LAB2-4, WR-P4B-1,
  WR-R4-1; P-10, P-11; P-08) and `BY_DESIGN` (R198's pilot-box worker case, R237's
  all-switches App gateway). Red first against the recorded 28c9c2cc verdict: open = o04, o05,
  e4-on and 4 journeys → `accepted: false`.
- o05's control-factory case: the Lab login's answers == the owner login's
  (`lab_world.judge_login`, R251); the `NOT RUN[SR-LCR-1]` marker dropped. Journeys run on
  `lab-on` (WR-LDP-1 landed): datasets, releases, evaluations.

## 2. Step 2: `make lab-local` for real

### First run at 1a5be321 (kept: `E4ON-raw-1a5be321/`, pins dirty — the raw dir itself)
Exit 1 FAIL, 1,413 s. Same as below except **o07 FAIL: `httpx.ConnectError [Errno 111]`** on
`https://localhost:57061`. Finding (owned test code): `lab_world.lab_web` waited only for
`next start` (ready in 125 ms) while the Caddy terminator had not bound its port yet (its log
stops at the root-cert install; the same failure in `E4ON-raw-950a570b`). Fixed at `fd0aba04`:
the Lab web is ready only once its https origin answers through the terminator (red first:
the test saw only `http://127.0.0.1:<LAB_PORT>/` waited on). Also: every earlier verdict said
`dirty: true` because the run writes its raw dir under research/plan/evidence; `pins()` now
excludes that path (red first: `True is False`).

### The run of record at fd0aba04: `GATE_ARGS=--keep make lab-local` → exit 2 (make) / runner exit 1 = FAIL
`research/plan/evidence/e/E4ON-raw-fd0aba04/verdict.json`; started 20:21:40Z, 2,106 s;
`pins: {head: fd0aba04…, dirty: false}`.

| Stage | Verdict | Detail |
|---|---|---|
| stack | usable | E2 services in e3l (postgres, valkey, clickhouse, s3), migrations through 0056 |
| lab-build | PASS | `pnpm build` (4.6 s, cached) |
| e4-on | FAIL (by design) | tests/g tests/w tests/contracts tests/i/test_packaging.py, every switch ON (13 switches): **2,853 tests: 2,838 passed, 1 failed, 0 errors, 14 skipped** (skips: other keys' PG/ClickHouse/t2f, named). The one failure is R198's `tests.w.test_worker_main::test_worker_main_pg__the_pilot_box_starts_the_real_worker_and_waits_for_it` (LDP-F4: the pilot-box worker inherits `LAB_EVAL_WORKER=true` and refuses by name). **The 35 WR-LDP-5 pins are green.** |
| scenarios | FAIL (by design) | o01 PASS, o02 PASS, o03 NOT RUN[P-11], o04 PASS, o05 FAIL (R237 case only), o06 PASS, o07 NOT RUN[product WR] |
| journey:datasets | PASS | `cd apps/lab && LAB_N_REAL=1 INFRX_D_TASK=lab-on node --test tests/n/journey.test.ts` |
| journey:releases | PASS | `cd apps/lab && LAB_R4_REAL=1 INFRX_D_TASK=lab-on node --test tests/r/stack.test.ts` |
| journey:evaluations | PASS | `cd apps/lab && LAB_B4_REAL=1 INFRX_D_TASK=lab-on node --test tests/b/stack.test.ts` |
| journey:pipelines | NOT RUN[WR-LL2-1] | backend binds p2's teacher-fake port 57529. Rerun: `cd apps/lab && LAB_P4_REAL=1 INFRX_D_TASK=p1 node --test tests/p/stack.test.ts` |
| journey:traces | NOT RUN[WR-LL2-2] | backend reads t2i's ClickHouse (57540). Rerun: `cd apps/lab && LAB_V1M_REAL=1 INFRX_D_TASK=lab-v1m node --test tests/v/list/stack.test.ts` |

Scenario cells:

- **o03** training NOT RUN[P-11] (refuses by name: no worker pass for the manual bundle,
  R198/R211); eval, checkpoints, judge, annotation, rollout, datasets PASS. Rerun:
  `tests/integration/lab-local.sh --reuse --only scenarios -k o03`.
- **o04** both cases PASS: the control factory serves a Lab session, and is ready on
  `infrx_lab_control` (LDP-F7 fixed).
- **o05** all-switches App gateway: FAIL — every family typed 503 on `infrx_runtime` — R237/R245
  by design (the box never runs it; `mutants.KNOWN_FAIL`). The Lab-routes gateway (owner
  login, ROLLOUT_ROUTING and TRACE_PUMPS false per WR-LC-LOCAL): NOT RUN[WR-B4-2, WR-LAB2-4,
  WR-R4-1] for evals/optimizations/pipelines; the rest 200. **The control factory on
  `infrx_lab_control` answers exactly as on the owner login** (`cases/composition/o05-control-families.json`):
  control, datasets, releases 200 on both; evals, pipelines, teacher-batches, optimizations the
  same typed `503 {"refusal":"unavailable"}` on both → NOT RUN[WR-B4-2, WR-LAB2-4, WR-P4B-1,
  WR-R4-1]; traces judged apart (404 on both: no ClickHouse backend in the control env). A
  consumer key is refused on every family: PASS.
- **o06** PASS: the consumer App path serves and settles once with every switch ON.
- **o07** NOT RUN[WR-B4-2, WR-LAB2-4, WR-R4-1]: the Lab web (production build, https origin)
  renders every page family signed in; /evaluations, /evaluations/checkpoints, /training,
  /optimizations render the typed "records could not be read" state
  (`cases/composition/o07-pages.json`). Rerun: `tests/integration/lab-local.sh --reuse --only scenarios -k o07`.

### R222 machine check (verdict.json `r222`)

`accepted: false`; `open: {journey:pipelines: NOT RUN, journey:traces: NOT RUN}`;
`by_design`: the R198 e4-on case and the R237 o05 case. Every other non-PASS cell names only
out-of-scope classes (product WR per port, P-11). **The gate is acceptable under R222 once
WR-LL2-1/2 land (or once the coordinator rules LL2-SCOPE, below).** No in-scope FAIL remains.

## 5. Wiring requests

- **WR-LL2-1** (owner: the lane owning `apps/lab/tests/p/`; test harness, not product):
  `apps/lab/tests/p/backend.py:109` binds the teacher fake to p2's port. Diff:
  ```diff
  -    fake = JudgeFake(port=local_services("p2")["teacher-fake"].host_port)
  +    fake = JudgeFake(port=int(os.environ.get("LAB_P4_TEACHER_PORT")
  +                              or local_services("p2")["teacher-fake"].host_port))
  ```
  Then (this lane, on landing): `runner.JOURNEYS["pipelines"]["foreign"] = None` and the
  journey env gets `LAB_P4_TEACHER_PORT=<lab_world.TEACHER_PORT>`. Test: journey:pipelines PASS in `make lab-local`.
- **WR-LL2-2** (owner: the lab-v1m lane, `apps/lab/tests/v/list/`; test harness): 
  `apps/lab/tests/v/list/backend.py:76-78` reads t2i's ClickHouse. Diff:
  ```diff
  -    ch = dict(host="127.0.0.1", port=tasklocal.local_services("t2i")["clickhouse"].host_port,
  -              username=CH_USER, password=CH_PASSWORD)
  -    admin = clickhouse_connect.get_client(**ch, database="infrx_t2i")
  +    url = os.environ.get("LAB_V1M_CLICKHOUSE_URL")      # http://user:pw@host:port/db
  +    if url:
  +        from urllib.parse import urlsplit
  +        u = urlsplit(url)
  +        ch = dict(host=u.hostname, port=u.port, username=u.username, password=u.password or "")
  +        admin = clickhouse_connect.get_client(**ch, database=u.path.lstrip("/"))
  +    else:
  +        ch = dict(host="127.0.0.1", port=tasklocal.local_services("t2i")["clickhouse"].host_port,
  +                  username=CH_USER, password=CH_PASSWORD)
  +        admin = clickhouse_connect.get_client(**ch, database="infrx_t2i")
  ```
  Then (this lane): `JOURNEYS["traces"]` runs with `LAB_V1M_CLICKHOUSE_URL=lab_world.clickhouse_url()`
  (the e3l block's ClickHouse). Test: journey:traces PASS.
- **WR-LL2-3** (runbook §11, applied here as allowed: only the G1–G6 and L5 rows + one log line):
  see `research/plan/consumer-v1/08-lab-internal-testing-rollout.md`.
- **WR-LL2-4** (finding, not this lane's code; base red at c77e75ac):
  `tests/integration/test_harness.py::test_nothing_in_this_directory_points_at_production` fails on
  `tests/integration/backend/test_certify.py` ('callbill.ai') and
  `tests/integration/ops/test_create_test_user.py` ('SUPABASE_SERVICE_ROLE_KEY', last touched
  8cc9c8fb). Owner: the TEST-USER / backend-certify lanes — split the needles as
  `lab_local/mutants.py` does, or allow-list the two files in the guard.
- Standing product WRs this gate re-runs on landing (R234 ii): WR-B4-2, WR-LAB2-4, WR-P4B-1,
  WR-R4-1 (o05/o07); P-11 (o03 training).

## 6. Proposed ruling (propose, never number): LL2-SCOPE

A Lab gate cell NOT RUN only because another lane's *test backend* hard-codes another tasklocal
key's port is a harness wiring request: it stays in `r222.open` (the runner does not excuse it)
until the WR lands — or the coordinator rules it out of local scope under R234 (ii), and this
lane adds WR-LL2-1/2 to `OUT_OF_SCOPE`.
