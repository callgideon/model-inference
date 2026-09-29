# LAB-CAPTURE (WR-C6-CAPTURE, LW6 lane lab-capture) - evidence at code head 5f6a0564

- Branch `codex/w5-lab-capture`, worktree `.claude/worktrees/codex-w5-lab-capture`, base `f9e6a14e`.
- Commits:
  - `fc79a3df` step 1: (a) consent source + the ingress policy seam;
  - `7ed2c889` step 2: (b) the request-path capture hook;
  - `5f6a0564` step 3: (c)+(d) the gateway ships (lifespan), the worker spools async output, the pilot/worker wiring;
  - this file + the coordinator update are the next commit.
- Keys:
  - t2f: PostgreSQL 57549 through the D harness; ClickHouse `infrx-t2f-clickhouse` 57543/57544 and MinIO `infrx-t2f-s3` 57545, started by this lane with the E2-pinned images and removed after the stack proofs; then 57545 reused as the E4/api-test Valkey `infrx-t2f-valkey`, started and removed by the D2 harness.
  - e5l for the o01 rerun (see step 5).
- No hosted Supabase, pilot box, AWS/SSM/S3, Vercel or secret touched. No migration: the consent read is a SELECT the gateway's pool (service_role) already has.
- Every switch stays OFF: `TRACE_PUMPS` defaults False (unchanged); with it off nothing in `gateway/capture.py` is built, `IngressDeps.capture`/`Lifetime.capture` are None and every changed line is inert (E4 below).

## What was built

`apps/infrx-api/infrx/gateway/capture.py` (new; the lane's capture/consent/policy module):

- **(a) consent.** `ConsentSource.policy(auth, now)`:
  - the key's own opt-in (`api_keys.trace_mode`, null = off, D3) under its org's consent head (`infrx.consent_history`, the highest version, revoked or not), the lower of the two;
  - revoked, not yet effective or no row: `off_mode_policy`;
  - evaluation consent only at `full`;
  - read at request time, cached per process per (org, key) for 60 s (the key cache's TTL), bounded (4096, oldest out);
  - fail closed: a failed read (the dedicated runtime login has no grant) is off, never an error.
- **(b) the hook.** `GatewayCapture.response` wraps a consented sync or SSE answer (`Captured`). It writes ONE record to the gateway's spool:
  - the request line (JSON: request id, model, messages, parameters), inline `data:` media replaced by its `data-sha256:` token;
  - a newline;
  - then every body message exactly as sent.
  - The caller's bearer token is scrubbed from both halves.
  - The record is finished on every way the answer ends; it is `content_complete` only when the answer finished.
  - `off` wraps nothing; `minimal` is metadata only (the sink's no-op capture); async is left to the worker.
  - A failure in open, add or finish is logged and never the request's.
- **(c) async output + shipping.** `capture_jobs(runner, ...)` puts a `JobCapture` in W's `AttemptRunner` (`jobs`, `put_result`).
  - It remembers each consented async attempt from `load_work` to `complete` (bounded to 1024).
  - After a completion the store accepted, it writes the record (the request line, then the output) into `TRACE_SPOOL_DIR/jobs/.<job id>-<n>`, a hidden spool of its own, sealed, then renamed visible.
  - A refused completion records nothing. A capture failure never fails the job.
  - `GatewayCapture.pump` (the pilot lifespan, every 10 s): flush, seal and ship the gateway's own spool, then every visible job spool the gateway can lock (`SpoolTraceSink`'s directory lock: a writer still holding one is skipped). A job spool is removed once shipped; one that did not ship stays, because `rmdir` refuses it.
- **(d) composition.** `adapters(settings, connect)` returns `{}` unless `TRACE_PUMPS`. `build` requires `TRACE_SPOOL_DIR`, `CLICKHOUSE_URL` and `S3_TRACE_BUCKET`, and composes:
  - C2's holds and T2I's `build_shipper` (the deployment's endpoint);
  - the gateway's `SpoolTraceSink` on `TRACE_SPOOL_DIR`: a second process on the same directory refuses to start, so there is one gateway process per `TRACE_SPOOL_DIR`;
  - the consent source on the job store's pool.

Wiring (small, inert when off):

- `infrx/gateway/routes/ingress.py` (owned): the `IngressDeps.capture` field (None by default). `Ingress.validated` admits `capture.policy(...)` in place of `off_mode_policy` when a capture is composed, and `chat` wraps the accepted answer through `capture.response(...)`. `/v1/jobs` goes through `validated`, so async jobs carry the consented policy to the worker.
- `infrx/gateway/pilot.py` (the brief's "wiring in pilot" and "the gateway lifespan ship"; declared below):
  - `adapters_from_env += **trace_capture.adapters(settings, connect)`;
  - `build_ingress_deps(capture=None)` → `IngressDeps(capture=)` and `Lifetime(capture=)`;
  - `lifespan` starts `capture.pump(stop)` and closes the capture (flush + seal) before the relay drain.
  - The hunks were placed so that every G mutant anchor still matches (anchor script: 357 anchors on the changed files, `BAD []`).
- `infrx/worker/__main__.py` (the trace_pumps role file):
  - `trace_pumps` keeps T3's retention and T2F's projection only. `build_shipper(limits, None, ...)`: no spool, no ship step, the gateway's directory is never locked, and `TRACE_SHIP_S` is gone.
  - `compose` calls `capture.capture_jobs(runner, TRACE_SPOOL_DIR, Wall)` when `TRACE_PUMPS` is on.

## Proposed ruling (unnumbered) - (c) where trace records are written and who ships

> **Trace capture writes where the output is produced; only the gateway ships (WR-C6-CAPTURE).** With `TRACE_PUMPS` on:
> - A request is admitted with the trace policy of its key's opt-in (`api_keys.trace_mode`, null = off) under its organization's consent head (`infrx.consent_history`, newest version; revoked or not yet effective = off). The lower of the two applies, it is read at request time and cached per process for at most 60 s, and a failed read is off.
> - A consented sync or SSE answer is recorded by the gateway in its own spool on `TRACE_SPOOL_DIR`: the request (inline media by digest, the bearer token never) and the answer as sent.
> - A consented async job's output is recorded by the worker in a spool of its own, `TRACE_SPOOL_DIR/jobs/<job id>-<n>`. It is hidden while written, renamed only once sealed and unlocked, and written after a completion the store accepted. There is one writer per directory (the sink's lock).
> - Only the gateway ships: every 10 s its lifespan seals and ships its own spool, then each job spool it can lock, and removes a job spool once every segment is acked.
> - There is one gateway process per `TRACE_SPOOL_DIR`: a second one refuses to start. The worker's `trace_pumps` keeps T3's retention and T2F's feedback projection only.
> - Off, nothing is built and every request is `off_mode_policy`.
> - A hosted enable needs the E4 regression rerun, both units mounting the same `TRACE_SPOOL_DIR`, and a read of `consent_history`/`api_keys` for the runtime login. The runtime login has none: the source fails closed to off. That read is a migration, `0054` LOCAL-ONLY, or a hosted window.

## Commands (from `apps/infrx-api` unless noted; logs in the lane scratch `lc/`)

| # | command | head | exit | result |
|---|---|---|---|---|
| 1 | `pytest -q tests/t/capture/test_capture.py` (step 1 tests first) | f9e6a14e+tests | 2 | RED: collection error, `infrx.gateway.capture` absent (`step1-red.log`) |
| 2 | same after step 1 | fc79a3df | 0 | 10 passed (11 with the moved runtime-login case) |
| 3 | `INFRX_D_TASK=t2f pytest -q tests/t/capture/test_capture_pg.py` | fc79a3df | 0 | 3 → 2 passed (2 PostgreSQL checks on every migration; the pure case moved to the fake suite) |
| 4 | `INFRX_D_TASK=t2f INFRX_MUTANTS=all pytest -q tests/t/capture/test_mutants.py` | fc79a3df | 0 | 27 passed: 17 subprocess + 5 in-process PG mutants killed, 0 survivors |
| 5 | step 2 tests first | fc79a3df+tests | 1 | RED: 8 failed (no `GatewayCapture`/`redacted`) (`step2-red.log`) |
| 6 | step 2 mutants, `INFRX_MUTANTS=all` | 7ed2c889 | 1 → 0 | first run: 3 broken (a `[x] = ...` unpack in 2 cases, an equivalent off-guard mutant) + 1 survivor (`hook_finish_raises`: the closed-sink case never raised) - the oracles were tightened (length asserts, a finish that raises, an off answer returned unwrapped) and a broken-stream case added; then **43 passed, 0 survivors** |
| 7 | step 3 tests first: `pytest -q tests/t/capture/test_capture.py` | 7ed2c889+tests | 1 | RED: 9 failed (no `capture_jobs`, `ship_once`, `pump`) (`step3-red.log`); pilot seam RED: `build_ingress_deps() got an unexpected keyword argument 'capture'` (`step3-pilot-red.log`); worker RED: `trace_ship` still composed (`step3-worker-red.log`) |
| 8 | `INFRX_D_TASK=t2f INFRX_MUTANTS=all pytest -q tests/t/capture/test_mutants.py` | 5f6a0564 | 1 → 0 | first run 69/74: 2 hangs (the pump case waited unbounded), 2 undeclared deaths (unpack/KeyError), 1 survivor (`ship_removes_unshipped_spools`: the segments guard was redundant with `rmdir`; guard removed, mutant re-cut to `rmtree`); then **74 passed: 64 subprocess + 5 PG mutants, 0 survivors** (`step3-mutants-final.log`) |
| 9 | `INFRX_MUTANTS=all pytest -q tests/w/test_worker_main_mutants.py` | 5f6a0564 | 0 | **88 passed, 7 skipped** (the PostgreSQL list needs `INFRX_M_S3_ENDPOINT`): incl. `main_trace_switch_ignored` (re-cut: the switch anchor now appears twice), `main_job_capture_switch_ignored`, `main_job_capture_not_installed`, `main_job_capture_elsewhere`, `main_trace_worker_locks_the_spool` (replaces `main_trace_ship_without_rotate`) |
| 10 | `INFRX_MUTANTS=all INFRX_D_TASK=t2f … pytest tests/g/test_mutants.py -k "<the 116 G mutants on pilot.py/ingress.py> or well_formed or every_case"` | 5f6a0564 | 0 | **118 passed**, 0 survivors |
| 11 | anchor script over every Python mutant list on `pilot.py`, `routes/ingress.py`, `capture.py`, `worker/__main__.py` | 5f6a0564 | 0 | 357+ anchors, `BAD []` |
| 12 | `INFRX_D_TASK=t2f INFRX_T2F_STACK=1 pytest -q -rs tests/t/capture tests/w/test_worker_traces_pg.py tests/t/ship tests/t/feedback --deselect tests/t/capture/test_mutants.py` | 5f6a0564 | 0 | **86 passed, 20 skipped** (9 T2I stack cases on the t2i block, 11 PG mutants outside `INFRX_MUTANTS=all`); incl. `test_capture_stack` (consent from PG → a job spool by `JobCapture` → `build(...).ship_once()` to real ClickHouse/MinIO with the admitted pins, content = request line + output, ORG_B finds nothing, the job spool removed, a second pass ships 0) and `test_worker_traces_pg` (the composed worker's retention/projection + the gateway's ship of an adopted segment) |
| 13 | **E4, every switch off**: `INFRX_D_TASK=t2f INFRX_D2_VALKEY_PORT=57545 INFRX_D2_VALKEY_CONTAINER=infrx-t2f-valkey .venv/bin/python -m pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | 5f6a0564 | 0 | **2822 passed, 27 skipped, 0 failed** (20m40s). This base collects 2849 cases in those paths, and base f9e6a14e collects the same 2849 (`--collect-only`). The brief's ≥ 2826 is the post-composition-6 tip's count (2856 collected), not reachable on this base. The skips are key/stack-scoped: MinIO unset (9), other keys' PG (b1/b3/p1/p2/r2/j2: 11), the t2f/t2i stacks (2), empty mutant parameter sets (3) |
| 14 | `INFRX_D_TASK=t2f INFRX_D2_VALKEY_PORT=57545 INFRX_D2_VALKEY_CONTAINER=infrx-t2f-valkey make api-test` (repo root) | 5f6a0564 | 1 | **6708 passed, 176 skipped, 9 xfailed, 43 failed** (1h31m). All 43 failures are `tests/i/test_mutants.py` `broken_runner: pristine baseline`: the i8 harness could not bind its fixed port 127.0.0.1:55450 (`docker run … infrx-i8-postgres` exit 125, "address already in use"). The port lies in the kernel's ephemeral range, and another process's outgoing connection held it (`ss`: TIME-WAIT 127.0.0.1:55450 ↔ 127.0.0.1:57502; the documented E2/D10 limit). No file under `tests/i`, `deploy/` or `infra/` changed. A rerun of `tests/i/test_observe.py tests/i/test_pooler.py` gave 22 passed and 6 errors from the same bind. The `infrx-i8-postgres` "Created" leftovers of this lane's two attempts were removed. Every other suite is green |
| 15 | E5L o01: `INFRX_E5L_PROJECT=e5l4 apps/infrx-api/.venv/bin/python tests/integration/lab_observe/runner.py --out <scratch>/o01-run1 --only o01` (repo root of a detached scratch tree at 5f6a0564 + WR-LC-O01 applied) | 5f6a0564 | 3 | **BLOCKED (not run)**. Preflight: "these task-local ports are already in use: postgres 57132, valkey 57179, clickhouse_http 57123, clickhouse_native 57190, s3 57100". A kept, idle foreign e5l stack holds them: `infrx-e5l-{postgres,clickhouse,s3,valkey}`, label checkout `…/scratchpad/rv-lo3/tests/integration`, the lab-observe-3 reviewer's, up 2 h at 17:0x UTC with no process attached. A project override moves compose names, never ports (R225), so e5l4 cannot sidestep it. Nothing foreign was touched; the runner's teardown removed nothing. Watched for ~2 h without release |

## Out-of-ownership edits (declared)

- `infrx/gateway/pilot.py`: 13 lines. The brief's steps 1 and 3 name "the wiring in pilot" and "the gateway lifespan ship". Every hunk is inert when `TRACE_PUMPS` is off, and every G anchor is kept. The coordinator may re-home them as a composition WR; the exact diff is `git diff f9e6a14e..5f6a0564 -- apps/infrx-api/infrx/gateway/pilot.py`.
- `tests/w/test_worker_main.py`, `tests/w/worker_main_mutants.py`, `tests/w/test_worker_traces_pg.py`: brief (c) moves the ship step out of `trace_pumps`, and these three files asserted it there. `TRACE_PUMPS` loses `trace_ship`. The trace case now asserts no spool, no lock, and the job capture on the runner, and `test_worker_traces_pg` ships through `gateway.capture.build`. The mutant list changes as in row 9.

## Wiring requests

- **WR-LC-O01 (lab-observe; lab-observe-3 owns these files).** Bind o01's switch case. The exact diff is in the raw file `LAB-CAPTURE-5f6a056-o01.diff` beside this one:
  - `scenarios_trace.py`: `test_o01_capture_turned_on_through_the_composition_switch` runs `TRACE_PUMPS=1` on both box processes over one `TRACE_SPOOL_DIR`, with the trip's ClickHouse and the trace bucket (prefix `infrx/`). alpha's consent is `full` and alpha's key opts in; beta's org consents, but beta's key does not.
    - alpha's sync answer and alpha's async job ship with their pins, their content holds the prompt and no secret, and beta finds nothing;
    - beta's request leaves no row;
    - no spool byte carries either secret, and `jobs/` is empty after the ship.
  - `runner.py`: o01 `lanes: []`.
  - `test_e5l_runner.py`: no unbound case remains.
  - `mutants.py`: `O01_ON`, plus two stack mutants on `infrx/gateway/capture.py`: `st_capture_switch_ignored` and `st_capture_key_opt_in_ignored`. `a_lane_undeclared` is re-cut onto o01's `"lanes": []` → `["COMPOSITION"]`, since no scenario waits on a lane any more.
  - Layer 1 on the scratch tree: `test_e5l_runner.py` + `test_mutants.py -k "not killed"` gives 21 passed; `INFRX_MUTANTS=all … -k "a_lane_undeclared or every_case"` gives 2 passed.
- **WR-LC-MAKE (Makefile).** Add `tests/t/capture/test_mutants.py` to `api-mutants`. Its PostgreSQL half runs in process with `INFRX_D_TASK` and skips visibly without Docker. Add `INFRX_D_TASK=t2f INFRX_T2F_STACK=1 … tests/t/capture/test_capture_stack.py` to the stack-proof lines (the `tests/w/test_worker_traces_pg.py` shape).
- **WR-LC-DOC (08 §5 row `TRACE_PUMPS`, `config.py` comment at `trace_pumps`, `deploy/preflight.py` `NOT_SETTABLE["TRACE_PUMPS"]`).** Replace "the worker runs T2I's shipper … Capture stays off: the gateway builds no trace sink either way" with the following text:
  > On: the gateway composes WR-C6-CAPTURE (`gateway.capture`): the per-key consent policy, the capture of consented sync/SSE answers into its spool on `TRACE_SPOOL_DIR`, and the ship pass in its lifespan every 10 s (its own spool, then the worker's job spools under `TRACE_SPOOL_DIR/jobs`). The worker runs T3's retention and T2F's feedback projection and spools consented async output. `TRACE_SPOOL_DIR`, `CLICKHOUSE_URL` and `S3_TRACE_BUCKET` are required by both, and there is one gateway process per `TRACE_SPOOL_DIR`.
  - Preflight: "runs trace capture + shipping in the gateway and retention + feedback projection in the worker (WR-T-4, WR-C6-CAPTURE, off by default): enabling it needs ClickHouse, the trace bucket, one shared TRACE_SPOOL_DIR for both units and the E4 regression rerun, a deploy change, not a --set".
- **WR-LC-HOSTED (coordinator, before any hosted enable; not needed locally).**
  - The dedicated runtime login (`infrx_runtime`, 0021) has no SELECT on `infrx.consent_history` or `public.api_keys`, so with it capture stays off (fail closed, tested). A hosted enable needs an additive read (an RPC `infrx.trace_consent(org, key)` EXECUTE `infrx_runtime`), `0054` LOCAL-ONLY until the R151 window.
  - Both systemd units must mount the same `TRACE_SPOOL_DIR`.

## Open issues

- `redacted` replaces any string value that starts with `data:`, so a text message beginning `data:` is spooled as its digest. That is conservative, never a leak. Narrow it to media parts if a customer's prompts start that way.
- A token split across two SSE frames is not scrubbed. This is marked ponytail: the relay's frames are whole JSON chunks.
- The worker builds one sink (and one writer thread) per consented async job. This is marked ponytail: move to one handoff directory per worker when a measured job rate asks for it.
- A job spool a crashed worker left hidden (`.`-prefixed) is never shipped or removed. The trace is lost and inference is not affected. A sweep of stale hidden spools is a follow-up.
- Pre-admission refusals (4xx before `accept`) write no record; there is no job to attribute them to. The capture spec's §4.6 refusal rows are not built.

## Step 5 - the o01 rerun: BLOCKED on the e5l ports (not a product result)

- o01's switch case is written and passes layer 1 (WR-LC-O01, raw diff beside this file); its stack run is BLOCKED as row 15 shows.
- Rerun once the e5l block is free (`docker ps | grep infrx-e5l` empty, or its owner releases it). From a tree with WR-LC-O01 applied:

      apps/infrx-api/.venv/bin/python tests/integration/lab_observe/runner.py --out <dir> --only o01

  Add `INFRX_E5L_PROJECT=e5l4` if the foreign `infrx-e5l_*` volumes are still present. The case must PASS. The real-service half of the chain has already passed on t2f (row 12): consent → job spool → the gateway's ship to real ClickHouse/MinIO with pins and the tenant bound.

## Estimate (remaining to merge)

Optimistic 1 h, likely 2.5 h, pessimistic 6 h; confidence medium. Basis:
- the three steps are done with fail-first seams, and every named mutant was killed, 0 survivors: 64 capture + 5 in-process PG, 88 worker, and 118 G on pilot/ingress;
- the E4 regression had 0 failed;
- the real-stack proofs passed on t2f;
- what remains is one verify round (the composition-3/-4/-5/-6 analogues each took 1-2 h), plus coordinator application of WR-LC-O01 and the o01 rerun once the e5l block is released (about 0.5 h: one `--only o01` run is about 15-25 min).
