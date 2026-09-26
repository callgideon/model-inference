# E3C-CELLS: DUR-FENCE, DUR-CAP and CREDIT-RATE carried by E3C s14, s15 and s16 (evidence)

Task E3C, a follow-up (BACKEND-LOCAL harness) for the App gate's three delegated cells that no
E3C scenario carried (E3A-RUN: `NOT RUN[delegated]`). Lane `codex/e3c-cells`, worktree
`.claude/worktrees/codex-e3c-cells`, base `ea29c1f1` (the integration tip).

- **Run head:** `9227e9ed`, clean at start and end (`verdict.json` `head` and `head_end`:
  `9227e9edc35dae658fa400d5ec84fed292c90028`, `dirty: false`). The evidence commit follows.
- **Verdict: BACKEND-LOCAL PASS.** Gate PASS, runner exit 0, make exit 0. 16/16 scenarios pass
  (s14, s15, s16 new); 12/12 negative controls pass, the five revert-type controls each
  detected for the right reason (the three new ones below). Deviations `[]`.
- Environment: the e3c block (56900–56999), task-local docker, `INFRX_E3C_RUNTIME_LOGIN=1` (the
  box on 0021's `infrx_runtime`). The e4b block, the hosted database, the pilot box, AWS and SSM
  were not touched. No secrets on argv or in logs; nothing pushed. No product code changed:
  `git diff --stat ea29c1f1..HEAD` is `tests/integration/backend/e3c/`,
  `tests/integration/app/runner.py` + `test_e3a_runner.py` and this lane's two evidence files.
- The oracles are [04-verification](../../04-verification.md) DUR-FENCE (line 17), DUR-CAP
  (line 16) and CREDIT-RATE (line 41).

## Changes

| Path | What |
|---|---|
| `tests/integration/backend/e3c/scenarios_fence.py` | s14 DUR-FENCE (2 cases) |
| `tests/integration/backend/e3c/scenarios_capacity.py` | s15 DUR-CAP (2 cases) |
| `tests/integration/backend/e3c/scenarios_rates.py` | s16 CREDIT-RATE (2 cases) |
| `tests/integration/backend/e3c/reverts.py` | the three SQL revert controls: one check removed by a last migration on a scratch tree |
| `tests/integration/backend/e3c/control_trees.sh` | builds the three new trees; the default list is now all five revert controls |
| `tests/integration/backend/e3c/runner.py` | `SCENARIOS` s14–s16, `CONTROLS` nc-dur-fence / nc-dur-cap / nc-credit-rate (revert-type), `REQUIRED` for the six cases and the eight new s12 cases |
| `tests/integration/backend/e3c/world.py` | `second_worker(box)`: a second worker process whose start cannot clear the first one's barrier marker |
| `tests/integration/backend/e3c/test_e3c_runner.py` | 8 new s12 cases (below) |
| `tests/integration/backend/e3c/README.md` | files rows and the cells table |
| `tests/integration/app/runner.py` | `DELEGATED` DUR-FENCE → `("s14",)`, DUR-CAP → `("s15",)`, CREDIT-RATE → `("s16",)`; the reference is this document at run head `9227e9ed` |
| `tests/integration/app/test_e3a_runner.py` | the carried cells tested; the NOT-carried rule kept on a stand-in entry |

## Design

Each scenario is real processes on real services (the composed E3C world: PostgreSQL with every
migration, PostgREST, Valkey, MinIO, the gateway and worker as their own processes on the
dedicated runtime login, E2's controlled engine), an explicit oracle, and a revert-type
negative control whose reverted tree must turn it red.

### s14 DUR-FENCE: a stale generation races the new one at every mutation

- **Injection.** A worker process holds right after its inference claim (`world.POINTS["claim"]`):
  it keeps generation 1's lease and stops renewing it, as a paused or partitioned process does.
  The lease lapses in real time (s05's `LEASES`: TTL 8 s, heartbeat 2 s). The reaper requeues the
  unpublished job, and a new generation claims it:
  - `another-process`: a second worker process (`world.second_worker`), another owner;
  - `same-process`: the held process's own second runner (`WORKER_CONCURRENCY=2`), the same
    owner, so only the generation tells the two leases apart.
- **The race.** Generation 2 streams (20 deltas, 0.5 s apart). The held process is released and
  races on its own path. Generation 1's token, the attempt row its claim minted
  (`infrx.lease_doc`), is presented at every door the worker mutates through, through the
  product's own adapters on the box's own login:
  - `PgStreamStore.append` (append output);
  - `PgJobStore.heartbeat` (renew);
  - `load_work_credit` (R46: fenced like a mutation);
  - `claim` and `PgLifecycle.claim_preparation` (a second inference or preparation slot);
  - `complete_credit` (settle).
- **Oracle.**
  - Each door answers the product's refusal: `stale_lease` from the fence, `not_claimable`
    from the claims.
  - Generation 2 completes `succeeded/completed` and settles exactly once (`settled_once`).
  - The wallet is conserved; no journal chunk of generation 1 exists.
  - The result read through the API is generation 2's full text.
  - Generation 1 was released before generation 2 was acquired (never two live inference
    attempts).
  - The engine served one generation.
  - The job's three reservations were released once.
- **Premise guards (INVALID, never PASS).** Generation 2 must be live and unsettled when the
  probes run; a refusal `already_terminal` means the new generation ended mid-probe.
- **Negative control `nc-dur-fence`.** The reverted tree gets
  `9999_e3c_nc_dur_fence.sql`: 0016 `infrx.fence_lease`, exactly as the tree defines it, with
  `if a.generation is distinct from (p_lease->>'generation')::int then` → `if false then`.
  The owner check stays. The control is therefore detected by the `same-process` case, while
  `another-process` still passes on that tree: the owner check alone refuses another process's
  stale lease. That is defense in depth, and it was measured.

### s15 DUR-CAP: concurrent admissions across keys and organizations at the caps

- **Keys.** Two organizations (alpha, beta), two consumer keys each; the second key is issued
  through the operator CLI (`issue-key`).
- **Capacity case.** 16 `POST /v1/jobs` (4 per key) are released at one barrier through two
  gateway processes. Caps: `MAX_ACTIVE_JOBS=4`, per organization 3, per key 2. No worker runs,
  so every admitted job stays active.
  - Oracle: exactly 4 are admitted, however the one admission lock orders them. (Every
    organization can reach 3 and its keys 4, so the platform cap always binds at 4.) No
    organization is past 3 and no key past 2.
  - The 12 refusals are the declared 429 `capacity_exhausted` with a numeric Retry-After. They
    leave no job and no idempotency mapping.
  - The rows grew by exactly (4 jobs, 4 held holds, 12 reservations, 4 dispatches, 4 mappings).
  - Available is never negative.
  - Then the worker drains: every job settles once and both wallets are conserved. A fifth
    admission is accepted afterwards, so the capacity was released.
- **Balance case.**
  - Setup: alpha's wallet is funded, through an audited operator adjustment, for exactly 2.5
    holds.
  - Burst: 8 alpha requests and 4 beta requests race one operator debit of one hold
    (`OperatorSession.adjust` over D5's `grant_credit`, in-process so it really lands inside the
    burst).
  - Oracle: alpha admits exactly what its wallet can hold, `admitted + debit_applied == 2`. The
    refusals are the declared 402 `insufficient_credit` and hold nothing.
  - Alpha's available ends at exactly half a hold, never negative, and its reserved total is
    exactly its holds. Beta is untouched.
  - After the drain the books are conserved with the adjustments counted.
- **Both cases.** Stable lock order: no `40P01` or `deadlock detected` in any answer, in the
  gateways' logs or in the namespace's PostgreSQL log since the burst (`docker logs --since`
  on the container `assert_ours` accepts), and the clone's `pg_stat_database.deadlocks` did not
  move.
- **Negative control `nc-dur-cap`.** `9999_e3c_nc_dur_cap.sql` is 0011 `infrx.admission_checks`
  with its three active-job cap comparisons off by one: `if v_count >= (p_limits->>'max_active_jobs`
  → `>`, three occurrences, for the total, per-organization and per-key caps. Detected by the
  capacity case. The hold check was deliberately not the one weakened: 0006's
  `credit_wallets_reserved_within_total` constraint backstops it. (That backstop was not
  measured here.)

### s16 CREDIT-RATE: a rate card and a deployment published while jobs wait and run

- **Setup.** The worker runs one inference at a time (`WORKER_CONCURRENCY=1`) and is held
  right after the running job's first committed chunk (`world.POINTS["output"]`). One job (A1)
  is therefore mid-execution, published and unsettled; one (A2) waits queued. Both are pinned to
  the seed card A.
- **Publication 1: `publish-card`** (G8's CLI, its own process): an approved card B (800 /
  2,400, twice A) for the listed deployment. Observed:
  - The gateway still configured for A admits nothing: a new request is 400
    `invalid_request` and no job is written (R69, S3 F11).
  - The gateway is restarted at `ACTIVE_RATE_CARD_VERSION=B` (G8 step 4). A same-key retry of
    A2 replays its original admission: 202, the same request id, `idempotency_replayed: true`
    (R91). A new job is admitted at B.
- **Publication 2:** a new serving revision (a new label, the same artifacts) and a new public
  deployment revision on the prod endpoint, priced by card C (1,600 / 4,800). It is one audited
  G6B publication that moves the alias (`OperatorSession.publish` on D5's registry: the service
  `publish-marlin` fronts). It is called in-process because that CLI verb mints an upper-case
  card id 0007 refuses (G8 open issue F11). The gateway is restarted at C and a new job is
  admitted with the new revisions and C.
- The worker is never restarted.
- **Oracle.**
  - After the release every job settles once, each at the revision and card it was admitted at:
    the running and the waiting job at (D1, S1, A), the job admitted between the publications
    at (D1, S1, B), the last at (D2, S2, C).
  - The usage row's card is the pinned card, and its charge is that card × usage.
  - The three charges differ, so a wrong card cannot hide.
  - The wallet is conserved.
  - Premise guard: the running and the waiting job must still be unsettled and queued when the
    publications are done.
- **Second case.** An unknown model, an unknown revision pin (`@no-such-revision`) and the
  provider's private dev endpoint name (`nemostation/marlin-2b-dev`) each get the same 404
  `not_found` (R70). A priced call still serves 200. The alias is then made unpriced (a newer
  listing whose card is not yet effective, E3B's `_unpriced_listing` shape, staged last on the
  disposable clone), and the next request is 400 `invalid_request` (R69). Nothing is admitted
  for any refused key.
- **Negative control `nc-credit-rate`.** `9999_e3c_nc_credit_rate.sql` is 0018
  `infrx.terminalize` with `infrx.debit_credit(j.rate_card_version, v_in, v_out)` replaced by a
  debit at the card of the alias's current listing (E3B db13's defect). Detected by the first
  case.

## Unit tests (no stack)

`cd apps/infrx-api && uv run --frozen --no-sync pytest -q ../../tests/integration/backend/e3c`
gives **46 passed** (38 before, plus 8 new). The new cases are all s12's and are in the
manifest:

- `test_s12_the_cells_carry_their_04_oracles_and_revert_controls`: one scenario and one
  revert-type control per cell;
- `test_s12_a_cell_scenario_passes_only_with_every_required_case[cap|fence|rate]`: PASS only
  when every required case passes. A red case is FAIL, a missing one NOT RUN, `BLOCKED[G8]`
  BLOCKED, and a HarnessError premise INVALID. The control stays NOT RUN without its tree;
- `test_s12_every_sql_revert_applies_to_this_tree[nc-dur-fence|nc-dur-cap|nc-credit-rate]`:
  the anchor occurs exactly as written in the tree's latest definition, the migration changes
  exactly those occurrences and sorts last, and a moved anchor refuses (`SystemExit`);
- `test_s12_every_revert_control_has_a_tree_builder`: `control_trees.sh`'s default list is
  every revert-type control.

Failed first:

- Base `ea29c1f1` plus the new test file and `reverts.py` (scratch archive): 5 failed.
  - the manifest test fails (the new s12 cases are not registered);
  - the cells test fails ("DUR-FENCE is carried by []");
  - the three cell-scenario cases fail.
- The new runner with base `control_trees.sh`: `…tree_builder` FAILED on the set mismatch. It
  passes with this lane's script.
- ruff: the new files are clean. The pre-existing F401 at `scenarios_crash.py:23` and E501 at
  `runner.py:554` are unchanged.

App side: `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/app` gives **19
passed** (was 18). On the base runner with the new test file:
`test_the_cells_e3c_now_carries_pass_only_on_their_own_scenario_rows` and the stand-in
NOT-carried test FAILED. `test_the_app_e2e_gate_runs_this_runner_and_takes_its_verdict` fails in
a bare `git archive` copy at base too, so that failure is environmental (it passes in the
worktree).

## Development runs (not the evidence run)

- `dev1` (`--only s14,s15,s16 --keep`): 6 passed in 83.5 s, first attempt.
- `dev2` (`--reuse`, the three new controls from a `9227e9ed` tree): 6 passed in 84.2 s. The
  controls were detected:
  - nc-dur-fence: 1 failed, 1 passed, 43.7 s;
  - nc-dur-cap: 1 failed, 1 passed, 16.1 s;
  - nc-credit-rate: 1 failed, 1 passed, 16.4 s.
  The reasons were the same as in the final run.
- An accidental duplicate launch at 06:32Z. An `&&` chain ending in `&` put a run in the
  background while a second launch was attempted, and the second one's `rm -rf` removed the
  first one's output directory. The first run was interrupted with SIGINT to its pytest at about
  2 minutes, so the harness's own `finally` closed its boxes, and the runner tore the stack down
  (0 containers, 0 listeners afterwards). No verdict from it is used. Its files moved to
  `…/scratchpad/cells/aborted-0632/`. The evidence run below was started afresh from a script.

## Final run

`SC=/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/cells`

- Command: the coordinator's recipe, from `$SC/final-run.sh` (detached). It checks that the
  tree is clean, then runs
  `CTL="$(tests/integration/backend/e3c/control_trees.sh HEAD $SC/ctl-final)"` (five trees) and
  `INFRX_E3C_RUNTIME_LOGIN=1 E3C_OUT=$SC/final /usr/bin/time -v make backend-local E3C_ARGS="$CTL"`.
- Started 06:34:31Z, ended 06:51:11Z. `/usr/bin/time -v`: **16:39.62 wall**, 248.73 s user +
  47.96 s sys, max RSS 139,288 kB, exit status 0.
- Verdict `$SC/final/verdict.json`, log `$SC/e3c-final.log`.
- Stages:
  - preflight PASS (1.0 s); services PASS (9.2 s); migrate PASS (0.6 s, 0001–0025);
  - backend-teardown PASS; teardown PASS (it removed the four `infrx-e3c-*` containers).
  - Afterwards: `docker ps -a | grep -c e3c` → 0, no listener in 56900–56999, no leftover box
    process, `git status --porcelain` empty.
- Sessions:
  - main: `99 passed in 765.73s`;
  - nc-admission-ready: `3 failed, 1 passed`;
  - nc-retention-durable: `2 failed, 1 passed`;
  - nc-dur-fence: `1 failed, 1 passed`;
  - nc-dur-cap: `1 failed, 1 passed`;
  - nc-credit-rate: `1 failed, 1 passed`.

### Scenario verdicts (from verdict.json)

| Scenario | Status | Cases |
|---|---|---|
| s01 identity → grant → key → modes → revoke; dataset resume | PASS | 2/2 |
| s02 two gateways, two tenants | PASS | 1/1 |
| s03 upload across gateway processes and SIGKILLs | PASS | 5/5 |
| s04 admission readiness | PASS | 4/4 |
| s05 crash at each step | PASS | 9/9 |
| s06 collectors / scrub | PASS | 3/3 |
| s07 persisted expiry on every read | PASS | 1/1 |
| s08 dependency outages | PASS | 3/3; PostgreSQL 503 in 20.0 s, S3 503 in 40.0 s (bound 42 s) |
| s09 CREDIT transition, lock bound | PASS | 4/4 |
| s10 runtime / browser / operator roles | PASS | 4/4 |
| s11 reconcile races | PASS | 3/3 |
| s13 discovery vs admission | PASS | 2/2 |
| **s14** stale generation race (DUR-FENCE) | **PASS** | **2/2**: another-process 24.9 s, same-process 25.4 s |
| **s15** admissions at the caps (DUR-CAP) | **PASS** | **2/2**: capacity 12.1 s, balance 8.9 s |
| **s16** rates published mid-queue (CREDIT-RATE) | **PASS** | **2/2**: publications 10.4 s, refusals 7.2 s |
| s12 the verdict itself | PASS | 45/45 |

### What the new cases measured (JUnit properties in verdict.json)

- **s14 `another-process`:** owners `worker-2884c7f3` (generation 1) and `worker-7a13d752`
  (generation 2). Stale probes: append `stale_lease`, renew `stale_lease`, load_work
  `stale_lease`, claim `not_claimable`, claim_preparation `not_claimable`, settle `stale_lease`.
- **s14 `same-process`:** owner `worker-6adc1b2d` for both generations (only the generation
  differs). The same six refusals.
- **s15 capacity:** alpha 1 + alpha-2 1 + beta 1 + beta-2 1 = 4 admitted; 12 × 429
  `capacity_exhausted`.
- **s15 balance:** alpha 1 admitted and 7 × 402 `insufficient_credit`; the racing operator
  debit landed (1 + 1 = 2); beta 4/4 admitted.
- **s16 publications:** usage (1,337, 5) for every job.
  - the running and the waiting job: `rc_marlin2b_2026_09_provisional`, 0.54080000 each;
  - the job admitted after publish-card: `rc_e3c_s16_b`, 1.08160000;
  - the job admitted after the deployment: `rc_e3c_s16_c`, 2.16320000.
- **s16 refusals:** unknown (404, `not_found`); unknown revision (404, `not_found`); private
  (404, `not_found`); unpriced (400, `invalid_request`).

### Controls

| Control | Status | Right reason |
|---|---|---|
| nc-journey-revoke, nc-journey-tenant, nc-upload-restart, nc-result-expiry (bypasses) | PASS | each bypass detected by its scenario's oracle |
| nc-roles-browser, nc-credit-cutover (DB defects), nc-verify-repro | PASS | detected |
| nc-admission-ready (tree `$SC/ctl-final/nc-admission-ready-9227e9ed`, owner login) | PASS | s04 on the reverted tree: text "executed before durable eligibility (RV-05): preparation 1, prepared 1, inference 1, debits 1"; video preparation claimed before eligibility; late rejection executed. The permanent-refusal case passes there, as expected. |
| nc-retention-durable (tree `…/nc-retention-durable-9227e9ed`) | PASS | s06: "a collector deleted a live job's media"; scrub: "the request record went 600797 s before its persisted retention"; dark PASS |
| **nc-dur-fence** (tree `…/nc-dur-fence-9227e9ed`) | **PASS** | s14 same-process on the reverted tree: "stale generation 1 accepted at ['append', 'load_work', 'renew', 'settle'] while generation 2 ran (DUR-FENCE)" (append, renew, load_work, settle ACCEPTED; the claims still `not_claimable`). another-process PASS there: the owner check refuses a foreign stale lease. |
| **nc-dur-cap** (tree `…/nc-dur-cap-9227e9ed`) | **PASS** | s15 capacity: "5 of 16 admitted at MAX_ACTIVE_JOBS 4 (DUR-CAP)"; the balance case passes there (caps not binding) |
| **nc-credit-rate** (tree `…/nc-credit-rate-9227e9ed`) | **PASS** | s16: a job "admitted at rc_marlin2b_2026_09_provisional settled 2.16320000 … (card x usage 0.54080000) (CREDIT-RATE)" (the current listing's card C); the refusal case passes there |

### Log tail (`$SC/e3c-final.log`)

```
    PASS  s01  verified identity -> CLI grant -> CLI key -> text/video sync/SSE/async -> result -> revoke
    PASS  s02  two gateways, two tenants, repeated callback/idempotency/finalize
    PASS  s03  upload create/PUT/finalize/resolve across gateway processes and restarts
    PASS  s04  empty manifest and late rejection: no execution before durable eligibility
    PASS  s05  crash at upload/admission/readiness/attachment/prep/outbox/claim/output/settle
    PASS  s06  two collectors with live jobs; expiry + policy change + replay; scrub content, keep metadata
    PASS  s07  persisted result expiry on every read across a policy change and restart
    PASS  s08  DB / object store / Valkey unavailable; process replacement
    PASS  s09  CREDIT enablement while historical USD jobs exist
    PASS  s10  runtime DB role, Supabase browser role, operator role denials
    PASS  s11  reconcile under active settlement / cancel / collector
    PASS  s13  discovery publishes only what admission serves (coordinator update 1)
    PASS  s14  stale generation race: a lapsed lease's generation at every mutation while the new one runs
    PASS  s15  concurrent admissions across keys and orgs at the capacity and balance caps
    PASS  s16  rate card and deployment published while jobs wait and run; unknown/private/unpriced refused
    PASS  s12  the verdict itself: missing service BLOCKED, skip never PASS, broken seam FAIL
    PASS  nc-journey-revoke  (bypass revoke-ignored (gateway))
    PASS  nc-journey-tenant  (bypass tenant-blind (gateway B))
    PASS  nc-upload-restart  (bypass upload-local (both gateways))
    PASS  nc-admission-ready  (revert the F2C/D10/W5/G7 readiness-barrier commits)
    PASS  nc-retention-durable  (revert the D10/M6 durable-liveness commits)
    PASS  nc-result-expiry  (bypass expiry-recompute (gateway))
    PASS  nc-roles-browser  (DB defect: INSERT on public.credit_ledger granted to authenticated)
    PASS  nc-credit-cutover  (DB defect: signup grant uniqueness dropped (E3B db09))
    PASS  nc-verify-repro  (classify(): a skipped / missing required case)
    PASS  nc-dur-fence  (revert fence_lease's generation check (0016))
    PASS  nc-dur-cap  (admission_checks's active-job cap comparisons off by one (0011))
    PASS  nc-credit-rate  (settlement debits at the current listing's card, not the admitted one (0018 terminalize))
gate PASS -> /tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/cells/final/verdict.json
	User time (seconds): 248.73
	System time (seconds): 47.96
	Elapsed (wall clock) time (h:mm:ss or m:ss): 16:39.62
	Maximum resident set size (kbytes): 139288
	Exit status: 0
```

## App gate rebinding (`tests/integration/app/runner.py`)

`E3C_FINAL` now names this document: run head `9227e9ed`, tip `ea29c1f1`, evidence
`research/plan/evidence/e3c/E3C-CELLS-9227e9e.md`. `delegated_reference` reads it unchanged: the
`**Verdict: BACKEND-LOCAL PASS.**` line, the run head, and one scenario row per id (the table
above).

| Cell | Scenarios | Reason (abridged) |
|---|---|---|
| DUR-CAP | `("s15",)` | concurrent admissions across keys/orgs at the capacity and balance caps; control nc-dur-cap |
| DUR-FENCE | `("s14",)` | a lapsed lease's generation refused at every mutation, in another process and in the same one; control nc-dur-fence |
| DUR-OUTBOX | `("s05", "s08")` | unchanged |
| CREDIT-RATE | `("s16",)` | card and deployment published mid-queue, each job settled at its admitted revision and card; unknown/private/unpriced refused; control nc-credit-rate |

With a green journey and this reference, the four delegated cells read
`PASS[delegated to E3C-FINAL 9227e9ed …]`, so APP-LOCAL can pass. No cell is `()` any more; the
NOT-carried rule is still tested on a stand-in entry. The App journey (e4b) was not run here;
that is the coordinator's rerun.

## Findings

- **F-1: the result object write is not fenced. Measured, minor, product (D).**
  - *Code:* `infrx.put_result` (0014:39-65) takes only `{job_id, text}`: first write wins and a
    different text is `state_conflict`. `AttemptRunner._settle` (`infrx/worker/attempt.py:477-488`)
    writes it before the fenced `complete`, and turns any failure into `platform_error`.
  - *Probe:* a scratch pytest in s14's world, `$SC/probe/probe_result_write.py`, not a gate
    case. Generation 1's token wrote a different text with `put_result` while generation 2
    streamed. The write was **accepted** (`infrx-result:…`). Generation 2 then ended **`failed /
    platform_error / released_platform_absorbed`**, and the stored result is generation 1's
    text.
  - *Reachability:* a real stale runner reaches `put_result` only with no visible output (an
    all-reasoning or empty answer) and a pause between its stream's end and the write. Any
    visible delta hits the fenced append first. No money moves wrongly (platform-absorbed) and no
    stale text is served; the harm is a lost success.
  - *Why not asserted:* 04's DUR-FENCE enumerates append, renew, settle and second capacity,
    which the brief listed, so s14 asserts those.
  - *Proposed fix (D10):* fence `put_result` with the lease (`fence_lease` first, as `append`
    does), or write the result inside `terminalize`.
  - *Oracle ready:* a `"result": "stale_lease"` door in s14's `REFUSED`, with a text that
    differs from generation 2's.
- **O-1: defense in depth, measured.** On the nc-dur-fence tree the owner check alone refused
  another process's stale lease at every door. Only a stale lease of the same worker id passed.
  0016's fence therefore needs the generation check exactly for the same-process case; worker
  ids are per-process (`worker-<uuid4[:8]>`).
- None of the new scenarios exposed a defect in the enumerated oracles. No product code was
  edited.

## Wiring requests

None: every changed path is owned. (For F-1 the proposal above is D10's, a new migration.)

## Proposed ruling text (next free R147; the coordinator numbers)

"A worker's result object is fenced output: `put_result` refuses a lease that is not the job's
live generation (as `append` does), or the result is written by the settling transaction.
Until then, a stale generation's result write can end the live generation `platform_error`
(E3C-CELLS F-1)."

## Remaining effort

| Optimistic | Likely | Pessimistic | Confidence | Basis |
|---|---|---|---|---|
| 0 h | 0.2 h | 1 h | high | The gate passes at `9227e9ed` with the three new scenarios and controls detected for the right reason. Left: the coordinator's merge and the App journey rerun (e4b) reading this reference. F-1 is a separate D10 item and does not block these cells. |

## Verification log

- 2026-09-26 (E3C-CELLS): s14/s15/s16 added with revert controls nc-dur-fence / nc-dur-cap /
  nc-credit-rate (`reverts.py`); e3c unit tests 46 passed; final run at `9227e9ed`:
  **BACKEND-LOCAL PASS**, 16/16 scenarios, 12/12 controls, 16:39.62 wall; App gate rebound
  (DUR-FENCE → s14, DUR-CAP → s15, CREDIT-RATE → s16; reference this document), App unit tests
  19 passed. F-1 (unfenced `put_result`) measured on a scratch probe and reported. No hosted DB,
  box, AWS or paid operation; nothing pushed.
