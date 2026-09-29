# LAB-CAPTURE-2 (T2, the capture carry-overs R7/R8/R9 + WR-LC-HOSTED; LW6 lane lab-capture-2) - evidence at code head 4f87f54c

- Branch `codex/w5-lab-capture-2`, worktree `.claude/worktrees/codex-w5-lab-capture-2`, base `7880f3c5` (after merge #54).
- Commits (one per step; tests first):
  - `bc94b420` step 1 (R8): the worker's job record is scrubbed of every minted credential;
  - `2c7e6e8f` step 2 (R7): a lost terminal ack keeps the attempt for the runner's retry;
  - `5e10fefc` step 3 (R9): request lines (inline media digests) are built off the event loop;
  - `89e4052a` step 4 (WR-LC-HOSTED): LOCAL-ONLY `0057_trace_consent_read.sql`, ConsentSource over it, `tests/d/test_code_mutants_lc2.py`, harness pin, Makefile line;
  - `4f87f54c` step 4 follow-up: `job_output_not_added` re-cut onto the scrubbed output line (step 1 moved its anchor);
  - steps 5-6 (this file, the WR diff, the update JSON) are the next commit.
- Key t2f only: PostgreSQL 57549 through the D harness; ClickHouse `infrx-t2f-clickhouse` 57543/57544 and MinIO `infrx-t2f-s3` 57545 started by this lane with the E2-pinned digests (`tests/integration/compose.yaml`) for the stack proof and removed after it; 57545 then reused by the D2 harness as `infrx-t2f-valkey` for E4/api-test. No other key's port or container touched.
- No hosted Supabase, pilot box, AWS/SSM/S3, Vercel or secret touched. `infrx/gateway/pilot.py` and `infrx/worker/attempt.py` unchanged (R7 is fixed in `JobCapture.complete`; the runner's retry already replays the identical completion). `TRACE_PUMPS` stays default OFF; with it off nothing in `gateway/capture.py` is composed (E4 below).

## What changed

`apps/infrx-api/infrx/gateway/capture.py`:

- **R8 (credential scrub on the worker).** The worker never holds the caller's bearer token, so the old `scrub(data, token)` with an empty token scrubbed nothing on the job path. `JobCapture` now runs `scrub_keys` (`KEY_SHAPE = sk-infrx-[A-Za-z0-9_-]+`, the family every platform key is minted in: `operations.service.new_secret`, the console's `lib/keys.ts`) over BOTH halves of a job record, every occurrence. Design pick: the worker scrubs by shape, so the raw token is never passed anywhere new and pilot.py needs no change (no WR). The gateway's sync/SSE path keeps its exact-token scrub.
- **R7 (lost ack).** `JobCapture.complete` pops the remembered attempt only after the store settled it, or on a `DomainError` refusal (then re-raises); any other failure (a lost ack) keeps it, so `AttemptRunner`'s identical retry (attempt.py ~510-529) that commits is traced exactly once. An attempt whose retry also fails stays remembered until the bounded `REMEMBER` (1024) evicts it.
- **R9 (hash off the loop).** `Captured` and `JobCapture` build the request line with `asyncio.to_thread(request_line, ...)`, so inline media (up to 96 MiB) is hashed off the gateway's and the worker's loop. `ponytail:` every request line takes the hop, not only one over 1 MiB; a size gate if a measured hop cost ever matters.
- **WR-LC-HOSTED.** `CONSENT_SQL` is `select * from infrx.trace_consent(%s, %s)` bound `(org, key)`; the SQL text moved into 0057.

`apps/app/supabase/migrations/0057_trace_consent_read.sql` (LOCAL-ONLY, additive, one function): `infrx.trace_consent(p_org uuid, p_key uuid)`, `SECURITY DEFINER`, `search_path = infrx, public, pg_temp`, returns at most one row for a key of `p_org` (the key's mode + the org's consent head, highest version revoked or not). `revoke all ... from public; grant execute ... to infrx_runtime` (service_role via 0004's defaults). Rollback: drop the function; the gateway then reads off (fails closed). Where 0057 is absent (hosted before its window) the dedicated login's read fails and capture is off - kept and tested (`check_without_the_grant_the_runtime_login_reads_off`).

## Checks (key t2f)

| # | command (apps/infrx-api unless noted) | head | exit | result |
|---|---|---|---|---|
| 1 | red first: the 6 new cases of `tests/t/capture/test_capture.py` against base `7880f3c5` code (detached scratch tree, HEAD's test file) | 7880f3c5 | 1 | **6 failed, 42 passed**: `test_an_async_jobs_record_holds_no_credential` (the reviewer's probe: `my key is <TOKEN>` + `echo <TOKEN>` left `sk-infrx-g1-test` verbatim in the job spool); `test_a_job_whose_first_ack_is_lost_is_traced_once_when_the_retry_commits` (0 job spools, `assert 0 == 1`); `test_a_lost_ack_whose_retry_is_refused_is_forgotten_untraced` (attempt dropped on the lost ack); `test_large_inline_media_is_hashed_off_the_{gateways,workers}_loop` (the 2 MiB part hashed with a running loop: `[True] == [False]`); `test_consent_is_read_through_the_trace_consent_rpc` (binds `(key, org)` to table SQL). 0057 red: `UndefinedFunction` (step 4 commit) |
| 2 | `INFRX_D_TASK=t2f pytest -q tests/t/capture/test_capture.py tests/t/capture/test_capture_pg.py` | 4f87f54c | 0 | **52 passed, 0 skipped** |
| 3 | `INFRX_MUTANTS=all INFRX_D_TASK=t2f pytest -q tests/t/capture/test_mutants.py` | 4f87f54c | 0 | **88 passed, 0 survivors** (6:00). New/re-cut: `job_credential_in_the_request`, `job_credential_in_the_output`, `job_scrub_first_only`, `job_scrub_one_shape_only`, `job_forgotten_on_a_lost_ack`, `job_refusal_remembered`, `hook_hashes_on_the_loop`, `job_hashes_on_the_loop`, `pg_reads_the_tables_directly`, `pg_runtime_refusal_raises`, `pg_revoked_head_in_force`, `pg_binds_in_the_wrong_order`, `sql_binds_in_the_wrong_order`, `job_output_not_added` |
| 4 | `INFRX_MUTANTS=all INFRX_D_TASK=t2f pytest -q tests/d/test_code_mutants_lc2.py` | 4f87f54c | 0 | **14 passed** (4 checks + well-formed + coverage + 8 SQL mutants killed: `lc2_runtime_grant_dropped`, `lc2_security_invoker`, `lc2_granted_to_public`, `lc2_browser_reads`, `lc2_key_of_any_org`, `lc2_oldest_head`, `lc2_head_skips_revoked`, `lc2_key_opt_in_ignored`) |
| 5 | stack proof: `INFRX_D_TASK=t2f INFRX_T2F_STACK=1 pytest -q -rs tests/t/capture tests/w/test_worker_traces_pg.py --deselect tests/t/capture/test_mutants.py` | 4f87f54c | 0 | **54 passed, 0 skipped** (incl. `test_capture_stack`: consent from PG through 0057 -> a job spool by `JobCapture` -> `build(...).ship_once()` to real ClickHouse/MinIO with the admitted pins, ORG_B finds nothing; and `test_worker_traces_pg`). A first run without the containers errored (connection refused 57543) - recorded, then the stack was started |
| 6 | (repo root) `pytest -q tests/integration/test_harness.py -k migration_set` | 4f87f54c | 0 | 1 passed (0057 pinned after 0054) |
| 7 | **E4, every switch off**: `INFRX_D_TASK=t2f INFRX_D2_VALKEY_PORT=57545 INFRX_D2_VALKEY_CONTAINER=infrx-t2f-valkey python -m pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | 4f87f54c | 0 | **2828 passed, 28 skipped, 0 failed** (28:10). 2856 collected; this lane changed no file under those paths, so the base collects the same. The skips are key/stack-scoped (other keys' PG b1/b3/p1/p2/r2/j2, MinIO/`INFRX_M_S3_ENDPOINT` unset, t2f/t2i stacks, empty mutant sets). The brief's >= 2830 is a later tip's count (composition-7's p3 run: 2831), not reachable on this base: 2856 - 28 = 2828 |
| 8 | D privilege suites without the WR: `INFRX_D_TASK=t2f pytest -q tests/d/test_reads.py tests/d/test_operator_d10.py tests/d/test_upgrade_d10.py tests/d/test_composition_pg.py` | 4f87f54c | 1 | **3 failed, 54 passed, 3 xfailed** - expected: `test_reads_privileges`, `test_operator_privileges`, `test_0025_is_re_runnable` pin the runtime login's function set (44) and 0057 adds `infrx.trace_consent(uuid,uuid)`. Fix = WR-LC2-RUNTIME below |
| 9 | same with `LAB-CAPTURE-2-WR-RUNTIME.diff` applied (detached scratch tree at 4f87f54c) | 4f87f54c+WR | 0 | **57 passed, 3 xfailed** |
| 10 | (repo root) `INFRX_D_TASK=t2f INFRX_D2_VALKEY_PORT=57545 INFRX_D2_VALKEY_CONTAINER=infrx-t2f-valkey make api-test` | 4f87f54c | 2 | **6861 passed, 3 failed, 187 skipped, 10 xfailed** (1:41:45). The 3 failures are exactly row 8's (`test_operator_privileges`, `test_0025_is_re_runnable`, `test_reads_privileges`: the runtime function set pin), fixed by WR-LC2-RUNTIME (row 9); every other suite green |

## Wiring requests

- **WR-LC2-RUNTIME (tests/d, not owned).** Apply `research/plan/evidence/t/LAB-CAPTURE-2-WR-RUNTIME.diff` with 0057: `checks_reads.RUNTIME_FUNCTIONS` gains `"infrx.trace_consent(uuid,uuid)"` and `checks_operator.check_operator_privileges` expects 45. Without it the three D privilege checks above fail on any tree carrying 0057 (row 8/9). Checked: `git apply --check` is clean on the tip `claude/consumer-v1` 33547abd (the tip's count is still 44).
- **WR-LC2-PIN (integration).** The harness pin places `0057_trace_consent_read.sql` after `0054_lab_release_live.sql`; on the tip it goes after `0056_lab_control_grants.sql`, filename order: `git merge-tree` of this head with the tip 33547abd reports exactly one conflict, in `tests/integration/test_harness.py` (this pin). 0057 is the next free number on this base; renumber at merge if taken (plan rule 3), with its references in `capture.py`'s comment, `tests/d/test_code_mutants_lc2.py`, the Makefile line and the harness pin.
- **WR-LC2-MAKE.** Already applied in the owned Makefile line (`api-mutants`: `tests/d/test_code_mutants_lc2.py`, key t2f, skips visibly without Docker).
- No pilot.py change needed (R8 is scrubbed by shape on the worker; no token or surrogate is passed).

## Proposed ruling (unnumbered) - amends R250's "credentials scrubbed" and consent-read sentences

> A trace record never holds a platform credential. The gateway scrubs the caller's own bearer token from both halves of a sync/SSE record; the worker, which never holds that token, scrubs every string of the minted key family (`sk-infrx-` + base62) from both halves of an async job's record. The request line (inline media by digest) is built off the event loop. A job's output is recorded once, after the completion the store accepted - including on the runner's retry after a lost terminal ack; a refused completion is never recorded. The consent read is `infrx.trace_consent(org, key)` (0057, EXECUTE for `infrx_runtime`, SECURITY DEFINER); where it is absent or refused the read fails and capture is off.

## Open / not done

- `TRACE_PUMPS` enable stays gated on: this lane's merge + WR-LC2-RUNTIME, 0057 applied only in its hosted window (plan rule 4: LOCAL-ONLY here), the E4 rerun on the enable tree, and E5L o01 (lab-observe-4).
- 0057 was not applied anywhere hosted.

## Estimate

Remaining to merge: 1 / 2 / 4 h (one verify round + the WR-RUNTIME/pin integration), confidence medium; basis: steps 1-5 done fail-first with 0 survivors (88 + 14), E4 0 failed, the t2f stack proof green; the merge-lane analogues (#54-#56) took 1-2 h each.

## Audit log

- 2026-09-29: written at code head 4f87f54c (steps 5-6 of lane lab-capture-2).

## Fix round (2026-09-29, from handback d12a6c3f)

Findings 0-LC2-CMO-1 and 1-LC2-INT-1 describe one item. The tests/d runtime-function pins
(`checks_operator.py:478` `len(held) == 44`, `checks_reads.RUNTIME_FUNCTIONS`) are outside
this lane's owned paths, so the lane cannot turn `make api-test` green on the branch. Both
reviewers call this a merge-time wiring item and not a seam defect. No code changed in this round.
Re-verified for the merge lane:

- `git apply --check LAB-CAPTURE-2-WR-RUNTIME.diff` passes on the tip `6738643c`, where line 478
  still reads `len(held) == 44`.
- Re-derived the count against the pending lanes. `codex/w5-lab-sql-lw9` (672bf2ac) adds
  0058_lab_variant_identities.sql, whose grants go to authenticated/service_role and the Lab
  roles, with none to infrx_runtime. `codex/w5-merge-62` (46fb5117) adds 0059_lab_control_grants_2.sql,
  which grants to `infrx_lab_control` only ("anon, authenticated and infrx_runtime gain nothing").
  The runtime set is therefore 45 after 0057+0058+0059. The diff stays correct as written.
- 0057 is not taken on the tip or on either pending lane (0058 and 0059), so no renumber is needed.

Merge instruction (unchanged): apply WR-LC2-RUNTIME in the same commit as 0057, and put the
WR-LC2-PIN harness line after 0056 (or after 0058/0059 if those merge first). Keep the files in
number order.
