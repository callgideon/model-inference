# E3C rerun on the 0024/0025 tree (codex/d10-merge-2 9e4e34ca): clean BACKEND-LOCAL PASS (evidence)

Task E3C. This is a harness-only rerun on a fixed lane SHA. Lane `codex/e3c-rerun`, worktree `.claude/worktrees/codex-e3c-rerun`.

- **Tree:** `9e4e34ca`, the head of `codex/d10-merge-2`: integration tip `34f0ed28` + `0024_console_read_port.sql` + `0025_operator_console.sql`, with W-D10A-1 v2, W-D10B-1 v2 and WR-D10B-2 already applied. Among those: the pgstate rows, the migration list through 0025, and E3C `SIGNED_IN_FUNCTIONS` with `consumer_credit_ledger`, `consumer_may_create_key` and the three `operator_*` RPCs.
- **Merge:** `git merge --no-ff --no-edit 9e4e34ca` → `8b824cd0`. **No conflicts.** The tree did not yet carry this lane's E3C-final harness (`27a69619`), so git merged the two automatically, including `scenarios_surface.py`.
- **Run head:** `8b824cd0`, clean at start and end. The evidence commit follows.
- **Harness changes beyond the merge: none.** The e3c unit tests passed on the merged tree (38 passed), and `control_trees.sh HEAD` built both trees unchanged. 0024/0025 do not touch `content_referenced`, and none of the admission revert commits conflict.
- **Verdict: BACKEND-LOCAL PASS** — gate PASS, runner exit 0, make exit 0; 13/13 scenarios; 9/9 controls, both revert-type controls detected for the right reason; deviations `[]`.
- e3c block (56900–56999), task-local docker, `INFRX_E3C_RUNTIME_LOGIN=1`. The e4b block, hosted DB, box, AWS and SSM were not touched. No secrets on argv or in logs; nothing pushed.

## Run

Command: `bash /tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/final-run.sh 9e4e34ca`. It does the merge (a no-op after step 1), `uv sync --frozen`, e3c unit tests (38 passed), `control_trees.sh HEAD`, then `INFRX_E3C_RUNTIME_LOGIN=1 /usr/bin/time -v make backend-local E3C_ARGS="--control nc-admission-ready=<tree> --control nc-retention-durable=<tree>"`, detached.

`SC=/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad`

- Started 05:02:17Z. Verdict: `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/final/verdict.json`. Log: `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/e3c-final.log`.
- `/usr/bin/time -v`: **12:16.27 wall**.
- Stages: preflight, services and migrate PASS. The migrate stage applies every file under `apps/app/supabase/migrations`, which on this tree is 0001–0025 (`pgstate.migration_files`). Every scenario's clone is built from those same files. backend-teardown and teardown PASS; afterwards `docker ps -a | grep -c infrx-e3c` → 0.
- Sessions: main `85 passed`; nc-admission-ready `3 failed, 1 passed`; nc-retention-durable `2 failed, 1 passed`.
- The previous final run's files moved to `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/final-27a69619/` and `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/e3c-final-27a69619.log`.

## Scenario verdicts (from verdict.json)

| Scenario | Status | Cases |
|---|---|---|
| s01 | PASS | 2/2 |
| s02 | PASS | 1/1 |
| s03 | PASS | 5/5 |
| s04 | PASS | 4/4 |
| s05 | PASS | 9/9 |
| s06 | PASS | 3/3 |
| s07 | PASS | 1/1 |
| s08 | PASS | 3/3: PostgreSQL 503 in 20.0 s, S3 503 in 40.0 s (bound 42 s) |
| s09 | PASS | 4/4 |
| **s10** | **PASS** | **4/4** (below) |
| s11 | PASS | 3/3 |
| s12 | PASS | 37/37 |
| s13 | PASS | 2/2 |

### s10 with the 0024/0025 surface

- **Runtime login:** `infrx_runtime` is no owner, rewrites no money, runs no DDL, and the box serves on it.
- **Browser surface:** `browser_surface` lists every `public`/`infrx` function `anon`/`authenticated` can EXECUTE, and every table/view they can INSERT, UPDATE, DELETE or TRUNCATE. It found nothing beyond the declared sets. With 0024/0025 installed, the `authenticated`-only functions are `consumer_jobs` (0024's widened signature), `consumer_job_result`, `consumer_credit_ledger`, `consumer_may_create_key`, `operator_adjust_credit`, `operator_set_suspension` and `operator_revoke_key`. `anon` executes none of them. The views `operator_wallet_drift` / `operator_unknown_usage` carry no browser write.
- **PostgREST deny cases, with their reasons:**
  - insert `credit_ledger`: 403 42501, permission denied for table
  - `api_keys` audience → operator: 403 42501, permission denied for table
  - claim another user's grant: 403 42501, permission denied for function
- **Signed-in reads (hosted `auth.uid()`, E3A-WR-2), through 0024's `consumer_jobs(p_request_id => …)`:**
  - own: 200, exactly the member's request;
  - another tenant: 200 `[]`;
  - another tenant's `consumer_job_result`: 400 P0001 `not_found`;
  - anon: 401 42501.
- **Operator/consumer credentials:** a consumer key cannot operate, an operator key runs no inference, and a revoked operator key operates nothing.
- **Not covered here:** E3C does not call the 0025 operator RPCs themselves (their `is_operator()` guard, idempotency and advisory lock). That is D10-0025's and U3's suite. s10 pins only who can execute them.

## Controls

| Control | Status | Reason |
|---|---|---|
| nc-journey-revoke, nc-journey-tenant, nc-upload-restart, nc-result-expiry, nc-roles-browser, nc-credit-cutover, nc-verify-repro | PASS | detected |
| **nc-admission-ready** (tree `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/ctl-final/nc-admission-ready-8b824cd0`, owner login) | **PASS** | s04 on the reverted tree: text "executed before durable eligibility (RV-05): preparation 1, prepared 1, inference 1, debits 1"; video preparation claimed early; late rejection executed and debited |
| **nc-retention-durable** (tree `/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/ctl-final/nc-retention-durable-8b824cd0`) | **PASS** | s06-live: "a collector deleted a live job's media"; scrub: the request record went 600,799 s before its retention |

## Log tail (`/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/e3c-final.log`)

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
	User time (seconds): 238.66
	System time (seconds): 39.79
	Elapsed (wall clock) time (h:mm:ss or m:ss): 12:16.27
	Maximum resident set size (kbytes): 121092
	Exit status: 0
```

## Verification log

- 2026-09-26 (E3C rerun on 0024/0025): 9e4e34ca merged as 8b824cd0 with no conflicts and no harness change. BACKEND-LOCAL PASS: 13/13 scenarios, 9/9 controls, both revert controls detected; s10 4/4 with the 0024/0025 functions; 12:16 wall. No hosted DB, box, AWS or paid operation; nothing pushed.
