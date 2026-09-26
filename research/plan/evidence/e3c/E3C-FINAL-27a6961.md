# E3C final BACKEND-LOCAL on the integration tip 04ae5e21: clean PASS (evidence)

Task E3C, phase 2, the final run. Lane `codex/e3c-rerun`, worktree `.claude/worktrees/codex-e3c-rerun`.

- **Tip:** `04ae5e21` (`claude/consumer-v1`: the backend union `d9e72c9c` with 0022, 0023 door revoke, W5 + admit_ready wiring, W5-F5, P25 enactment; plus W5-F5B `009af6b1` with WR-W5F5B-1). Merged `--no-ff` into `codex/e3c-rerun` as `601f4c1d`. The merge was clean.
- **Run head:** `27a69619`, clean at start and end. The evidence commit follows.
- **Verdict: BACKEND-LOCAL PASS.** Gate PASS, runner exit 0, make exit 0. All 13 scenarios pass; all 9 negative controls pass; both revert-type controls are detected for the right reason. Deviations `[]`.
- Environment: the e3c block (56900–56999), task-local docker, `INFRX_E3C_RUNTIME_LOGIN=1` (the box on 0021's `infrx_runtime`). No hosted DB, box, AWS or SSM; no secrets on argv or in logs; nothing pushed.

## Changes on codex/e3c-rerun for this run

| Commit | What |
|---|---|
| `601f4c1d` | merge of `04ae5e21` |
| `6fabd035` | **E3A-WR-1:** `world.need_stack()` compares `harness.NAMESPACE` with `INFRX_E2_NAMESPACE` (default e3c), not a constant. **WR-W5F5B-3, option (b), ruling R139:** s04 late rejection. On the readiness door (the marker is committed at the hold) the post-marker withdrawal is answered 202, and the job runs to `succeeded` and settles once. Where no marker exists (the pre-D10 door) the case keeps the refusal: 400/404/409/415, `cancelled`, nothing executed. s10 (for WR-2's re-check, below): the browser deny cases record `status code: message` as JUnit property `denials`, and a new case `test_s10_a_signed_in_consumer_reads_its_own_jobs_and_no_others` is added to the s10 REQUIRED manifest. |
| `b774ce23` | **E3A-WR-2:** `stack._template` applies the hosted `auth.uid()` (reads `request.jwt.claims`; the same text as `tests/integration/app/runner.py` `HOSTED_AUTH_UID`, now also `stack.HOSTED_AUTH_UID`). It runs as `supabase_admin` in the same `docker exec psql` as the `auth.users` columns. E3A's runner still applies its per-clone copy; dropping that belongs to E3A. |
| `7c4101af` | `control_trees.sh` follows the tip. nc-admission-ready also reverse-applies W5-F5B's post-marker attach (`0a230353`) and W5-F5's no-recheck (`e592c6d0`). The worker's readiness argument is stripped in either spelling (`readiness=lifecycle` since W5-F5B `6dbaae64`). `d58139f3`'s pilot hunk is rebased over WR-P25-1 (`2e6931ea`, `_pg_lifecycle(connect, settings.pilot)` → `settings`). |
| `27a69619` | runner: the admission control runs its reverted tree on the owner login (`CONTROLS["nc-admission-ready"]["env"]`), and a control with any INVALID case is INVALID. Two s12 regressions. Reason: final run 1, below. |

`pytest tests/integration/backend/e3c` (no stack): **38 passed**. The pre-existing ruff F401 at `scenarios_crash.py:23` is unchanged.

## E3A-WR-2: s10 deny cases, before and after

Both runs used `INFRX_E3C_RUNTIME_LOGIN=1 runner.py --only s10`. Before (WR-1 and WR-3 applied, WR-2 not yet): `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/s10-before/verdict.json`. After (`b774ce23`): `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/s10-after/verdict.json`.

| s10 case | Before WR-2 (pinned image's `auth.uid()`) | After WR-2 (hosted `auth.uid()`) |
|---|---|---|
| browser: insert `credit_ledger` | 403 42501 permission denied for table credit_ledger | same |
| browser: `api_keys` audience → operator | 403 42501 permission denied for table api_keys | same |
| browser: claim another user's grant | 403 42501 permission denied for function claim_signup_grant | same |
| signed-in: own `consumer_jobs` | **403 42501 "not signed in"** → case **FAIL** | 200, exactly the member's request id |
| signed-in: another tenant's `consumer_jobs` | 403 "not signed in" (a **vacuous** deny: no user was seen) | 200 `[]` (denied for its own reason: not its org) |
| signed-in: another tenant's `consumer_job_result` | 403 "not signed in" (vacuous) | 400 P0001 `not_found: no result for request …` |
| `anon` `consumer_jobs` | 401 42501 permission denied for function | same |

- The three existing browser deny cases deny by privilege, both before and after WR-2. They never depended on `auth.uid()`, so they were not vacuous.
- The tenant-scoped consumer reads (0021) did deny vacuously before WR-2. The new case fails on the pinned `auth.uid()` and passes on the hosted one.
- s10 result: before, 3/4 (the new case FAIL); after, **4/4**.

## Final runs

Command, as staged: `bash /tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/final-run.sh 04ae5e21`. It does the merge (a no-op), `uv sync --frozen`, the e3c unit tests, the control trees from the merged HEAD, then `INFRX_E3C_RUNTIME_LOGIN=1 setsid nohup /usr/bin/time -v make backend-local E3C_ARGS="--control nc-admission-ready=<tree> --control nc-retention-durable=<tree>"`, detached.

`SC=/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad`

1. **Launch 0 (`6fabd035`/`b774ce23`): stopped before anything ran.** `control_trees.sh` refused: "reverting d58139f3 does not apply on HEAD". W5-F5 and W5-F5B had changed `relay.py` and `pilot.py` since the builder was written. Fixed in `7c4101af`.
2. **Final run 1 (`7c4101af`): gate PASS, but not accepted.** 14:26 wall; 84 passed. Verdict `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/final-run1-7c4101af/verdict.json`, log `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/e3c-final-run1-7c4101af.log`.
   - nc-admission-ready read PASS, but not for the right reason. On the reverted tree three s04 cases were INVALID[harness] (the gateway never reached its fault point), and the fourth met a 503.
   - Diagnosis from the tree's gateway log: `psycopg.errors.InsufficientPrivilege: permission denied for function admit`. The reverted tree admits through the pre-D10 `infrx.admit`, which 0021 does not grant `infrx_runtime` (R127: it admits only through `admit_ready`). So on the runtime login every admission was a 503, and the control judged nothing.
   - Harness fix (`27a69619`): that control runs the pre-D10 composition on the owner login, as it ran before 0021. The runner now treats any INVALID case as an INVALID control. Validation: `runner.py --only s12 --control nc-admission-ready=…` gave s04 FAIL for the right reason (`/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/v3/verdict.json`).
3. **Final run 2 (`27a69619`): the evidence run. Gate PASS.** Verdict `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/final/verdict.json`, log `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/e3c-final.log`.
   - Started 03:52:46Z.
   - `/usr/bin/time -v`: **11:55.37 wall**, 227.20 s user + 38.23 s sys, max RSS 119,136 kB.
   - Stages: preflight, services, migrate PASS; backend-teardown and teardown PASS; afterwards `docker ps -a | grep -c e3c` → 0.
   - Sessions: main `85 passed` (runner exit 0); nc-admission-ready `3 failed, 1 passed`; nc-retention-durable `2 failed, 1 passed`.

### Scenario verdicts (final run 2, from verdict.json)

| Scenario | Status | Cases |
|---|---|---|
| s01 identity → grant → key → modes → revoke; dataset resume | PASS | 2/2 |
| s02 two gateways, two tenants | PASS | 1/1 |
| s03 upload across gateway processes and SIGKILLs | PASS | 5/5 |
| **s04** admission readiness | **PASS** | **4/4**: text PASS, video PASS, late rejection PASS (readiness door: 202, succeeded, settled once, per R139), permanent refusal PASS (`preparation_failed` at the first attempt) |
| s05 crash at each step | PASS | 9/9 |
| s06 collectors / scrub (worker housekeeping) | PASS | 3/3 |
| s07 persisted expiry on every read | PASS | 1/1 |
| s08 dependency outages | PASS | 3/3; PostgreSQL 503 in **20.0 s**, S3 503 in **40.0 s** (bound 42 s: R130's 40 s plus the 2 s client margin) |
| s09 CREDIT transition, lock bound | PASS | 4/4 |
| **s10** runtime / browser / operator roles | **PASS** | **4/4**: runtime login locked down and serving; browser denials by privilege; signed-in reads own rows only (hosted `auth.uid()`); operator/consumer lanes |
| s11 reconcile races | PASS | 3/3 |
| s12 the verdict itself | PASS | 37/37 |
| s13 discovery vs admission | PASS | 2/2 |

### Controls

| Control | Status | Right reason |
|---|---|---|
| nc-journey-revoke, nc-journey-tenant, nc-upload-restart, nc-result-expiry (bypasses) | PASS | each bypass detected by its scenario's oracle |
| nc-roles-browser, nc-credit-cutover (DB defects), nc-verify-repro | PASS | detected |
| **nc-admission-ready** (tree `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/ctl-final/nc-admission-ready-27a69619`, owner login) | **PASS** | s04 on the reverted tree: text "executed before durable eligibility (RV-05): preparation 1, prepared 1, inference 1, debits 1"; video: preparation claimed before eligibility; late rejection: executed and debited. The permanent-refusal case passes there, as expected: it is W5 item 3, not the barrier. |
| **nc-retention-durable** (tree `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/ctl-final/nc-retention-durable-27a69619`) | **PASS** | s06 on the tree: "a collector deleted a live job's media: media/…/source"; scrub: "the request record went 600799 s before its persisted retention"; dark PASS. |

### Log tail (`/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/e3c-final.log`)

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
gate PASS -> /tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/final/ve
	User time (seconds): 227.20
	System time (seconds): 38.23
	Elapsed (wall clock) time (h:mm:ss or m:ss): 11:55.37
	Maximum resident set size (kbytes): 119136
	Exit status: 0
```

## Ruling text for the coordinator

R139, as W5-F5B worded it (`research/plan/evidence/w/W5-F5B-406cd93.md`), is what s04's late-rejection case now pins on real processes. The readiness door answers the committed outcome: 202, run, settled once. The pre-D10 door keeps its refusal: cancelled, nothing run.

## Open (not gate cases)

- E3A's runner (`tests/integration/app/runner.py`) can now drop its `world.NAMESPACE = NAMESPACE` shim (E3A-WR-1) and its per-clone `HOSTED_AUTH_UID` step (E3A-WR-2); E3A owns both.
- The earlier finding F-6 (reconciliation gauges on the runtime login): W5-F5 `3a6195b2` reads them on D10's monitor login. Not re-examined here.

## Remaining effort

| Optimistic | Likely | Pessimistic | Confidence | Basis |
|---|---|---|---|---|
| 0 h | 0 h | 0.5 h | high | E3C's gate passes on the integration tip with both revert controls detected. Left: the coordinator's merge of `codex/e3c-rerun`. A rerun is needed only if the tip moves under the relay or worker composition again (the control builder then needs its revert list updated). |

## Verification log

- 2026-09-26 (E3C final): tip 04ae5e21 merged as 601f4c1d. Wirings applied: E3A-WR-1, E3A-WR-2 (s10 denials before and after, recorded above), WR-W5F5B-3 option (b) (R139). Control builder and runner fixed for the tip. Final run 2 at 27a69619: **BACKEND-LOCAL PASS**; 13/13 scenarios; 9/9 controls; revert controls detected for the right reason; 11:55 wall. Final run 1 (7c4101af) rejected: its admission control could not judge on the runtime login. No hosted DB, box, AWS or paid operation; nothing pushed.
