# LAB-LIVE (WR-C6-LIVE under R244, task R2) - lane lab-live, head 1f68d35

- Base `993d481c` (merge #50). Branch `codex/w5-lab-live`, worktree `.claude/worktrees/codex-w5-lab-live`.
- Commits: `7b689871` (1) 0054 + SQL list, `0c1d40c8` (2) the read port, `90843622` (3) the rollout pass, `1f68d35` (4) Makefile line + E4, then this evidence (5).
- Keys: r2 (PG 57534) for every PG case and E4; r1 (PG 57532, Valkey 57533 as `infrx-r1-valkey`) for a parallel D/R rerun and E4's Valkey. No other lane's container was touched; none are left.
- Every Lab switch stays OFF. Nothing was applied hosted. 0001-0053 are untouched.

## Done

1. **`0054_lab_release_live.sql`** (LOCAL-ONLY, R150/R151/R201; header, rollback and re-run notes like 0053's). `infrx.lab_release_live {policy_ref}` returns one row per arm (`baseline`, then `candidate`). The candidate arm is every assignment not on the revision's `baseline_ref`. The rows are built from this revision's `lab_rollout_assignments` joined to `infrx.jobs`:
   - `requests`: terminal jobs. `errors`: failed + expired (a cancel is not an error).
   - `p99_ms`: `percentile_disc(0.99)` of `settled_at - admitted_at`, in ms.
   - `spent {unit, value}`: CREDIT = the job's `inference_debit` (the v1 debit is 0 for CREDIT, R87); USD = a legacy_usd job's debit. Jobs of both regimes are refused with `invalid_request` ("units never mix").
   - `quality_covered`: requests with a `customer` or `operator` feedback row. A judge's row does not count.
   - `candidate_healthy`: every candidate's deployment revision (the R188 ref's id) is `ready_private` or `active`. A retired or unknown one is false.
   - `observed_until`: `infrx.now()`.
   - A revision with no assignment naming an admitted job returns `[]`. An unknown revision returns `not_found`. Execution is service_role only, through 0004's defaults.
   - Harness pin: `tests/integration/test_harness.py` gains one line.
2. **The R2 read port.** `PgReleaseStore.live(policy_ref)` (`infrx/state/lab_rollout.py`) shapes the rows into R2's `Live`:
   - arms by name; the database clock;
   - the **candidate arm's** quality coverage and spend (see the ruling proposal);
   - `None` for `[]`;
   - a non-Lab unit (legacy USD) refused as `InvalidRequest` by name, never converted.
3. **The rollout pass** (`infrx/lab/workers/__main__.py`, rollout pass only). `NoLive` is replaced by `release_live`, which is D9's Live of the listed revision on the role's pool. When nothing is assigned it raises `DependencyUnavailable`, so the release is held, never evaluated on zeros.
   - A running release is now stepped through R2's `evaluate` (R228), so the hold/rollback verdicts run over real data.
   - A plan budgeted in another unit than the jobs settled in is refused by R2's `evaluate` (`InvalidRequest`) and counted `failed`, with no decision.
   - The loop body is unchanged, so every E8L anchor stays valid.
4. **Makefile** `api-mutants` += the 0054 SQL list line (r2). `make -n api-mutants`: 11 pytest lines (was 10). `make -n lab-compositions`: 13 pytest lines, unchanged. `tests/r/control/test_control_pass_pg.py` is already on it and now proves the pass on real Live.

## Fail-first (red, recorded before each implementation)

| step | command (apps/infrx-api) | result before |
|---|---|---|
| 1 | `INFRX_D_TASK=r2 pytest tests/d/test_code_mutants_live.py -k "test_release_live or well_formed or every_case"` (no 0054) | 6 failed / 1 passed. Every check hit `42883 function infrx.lab_release_live does not exist`; well_formed hit FileNotFoundError on 0054 |
| 2 | same `-k test_release_live` with the store assertions (0054 present, no port) | 3 failed: `AttributeError: 'PgReleaseStore' object has no attribute 'live'` |
| 3 | `INFRX_D_TASK=r2 pytest tests/r/control/test_control_pass_pg.py` (real assignments + jobs, nothing injected) | 1 failed: the bare pass held (`{'stepped': 0, 'held': 1}`) instead of stepping on Live |

## Checks (green)

| # | command | head | exit | result |
|---|---|---|---|---|
| 1 | `INFRX_D_TASK=r2 uv run --frozen pytest -q tests/d/test_code_mutants_live.py` | 7b689871 | 0 | 27 passed: 5 PG checks, 2 list checks, **20 SQL mutants killed** (commit 1's message says 21; the list has 20) |
| 2 | `uv run --frozen pytest -q tests/r/control/test_control.py -k live`; `INFRX_MUTANTS=all … tests/r/control/test_mutants.py -k "live or well_formed or every_case"` | 0c1d40c8 | 0 | 1 passed; 9 passed (7 new port mutants killed, anchored in `state/lab_rollout.py`) |
| 3 | `INFRX_D_TASK=r2 pytest tests/r/control/test_control_pass_pg.py` | 90843622 | 0 | 1 passed. The case: nothing assigned is held; 10 healthy candidate jobs are held (no decision); 1 failed job (1/11 > 2%) rolls back once by LAB_OPERATOR_ID with `["error_rate"]`; the next pass only converges; the alias stays on the baseline; a PROVIDER_USD plan over CREDIT jobs is `failed`, running, undecided |
| 4 | `INFRX_MUTANTS=all .venv/bin/python -m pytest -q tests/w/test_lab_workers_mutants.py tests/r/control/test_mutants.py` | 90843622 | 0 | **219 passed, 0 survivors** (3 new `lw_rollout_*` + 1 re-cut `lw_rollout_live_by_default`; 7 new `r2_live_*`) |
| 5 | **E4, every switch OFF:** `INFRX_D_TASK=r2 INFRX_D2_VALKEY_PORT=57533 INFRX_D2_VALKEY_CONTAINER=infrx-r1-valkey .venv/bin/python -m pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | 90843622 | 0 | **2830 passed, 26 skipped, 0 failed** (24m02s; the floor is ≥ 2828). The skips are key/stack-scoped as in COMPOSITION-6 (other keys' PG, the t2f proof, empty mutant parameter sets) |
| 6 | `INFRX_D_TASK=r1 pytest -q -rs tests/d/test_code_mutants_live.py tests/d/test_c6_reads.py tests/d/test_d9_release.py tests/d/test_d9_rollout.py tests/d/test_d8_requests.py tests/d/test_d7_lab_data.py tests/d/test_upgrade_lab.py tests/d/test_port_d10.py tests/r` | 90843622 | 0 | 181 passed, 0 skipped (incl. the 0054 SQL list again on r1, and R1's routing PG) |
| 7 | `INFRX_D_TASK=r1 pytest -q tests/d/test_reads.py` (the runtime's exact function surface) | 90843622 | 0 | 42 passed, 3 xfailed (0054 is not executable by infrx_runtime) |
| 8 | `pytest tests/i/test_known_good_proof.py tests/i/test_migrate.py tests/i/test_release_bundle.py tests/i/lab_pipeline tests/i/lab_rollout tests/i/lab` | 90843622 | 0 | 112 passed, 1 xfailed (0054 does not move the R151 proofs) |
| 9 | `pytest tests/integration/lab_{rollout,improve,local,evaluate}/test_mutants.py -k "well_formed or every_case or anchor or declared or known"` (repo root) | 90843622 | 0 | 12 passed. Every E8L anchor on the pass (`live(item) if …`, `except errors.DependencyUnavailable`, `releases_in(("running", "rolled_back")`, `plan_key`) is intact |
| 10 | `pytest tests/integration/test_harness.py` (repo root) | 7b689871 | 1 | 46 passed, 1 failed: `test_nothing_in_this_directory_points_at_production`, which flags `test_certify.py`/`mutants.py`/`test_create_test_user.py`. Those files are not this lane's and the case is unrelated to the pin; the pin case (`…migrations…`) passes |
| 11 | `uv run --frozen ruff check` on every changed .py | 90843622 | 0 | All checks passed |

- `make api-test` (the whole suite) was not run; rows 1-9 cover the affected tracks.
- Test cases added: `tests/d/test_code_mutants_live.py` (5 checks, each named by a SQL mutant; `test_every_case_is_covered_by_a_mutant` passes), `tests/r/control/test_control.py::test_r2_live_is_read_per_arm_from_d9s_release_read` (named by 7 mutants), `tests/w` (the rollout case extended and named by 3 new mutants), `tests/r/control/test_control_pass_pg.py` (rewritten; outside the runner like before, its fake-level oracles are the `lw_rollout_*` mutants).
- Fixture note: job, ledger and feedback rows are written with `session_replication_role = replica` (the `tests/content/world_pg.py` pattern). The read is under test here, not admission or settlement.

## Wiring requests (none applied)

- **WR-LIVE-K09 (lab-rollout-5, `tests/integration/lab_rollout`)**: "let it see a breach" now binds over Live. Exact diff for `scenarios_recover.py` below. It keeps k09's held-on-the-first-pass proof, which keeps `st_pass_held_is_failed` killed. It then assigns one failed terminal job to the running release's candidate, and the next pass (≤ ROLLOUT_PASS_S later) rolls it back once with `["error_rate"]`. It was not run here (the e8l stack is not this lane's).
  - It also needs `runner.py` `SUB_CELLS`: drop `"k09-breach"` (bound now).
  - `test_e8l_runner.py::test_e8l_k09s_breach_half_is_a_not_run_sub_cell_with_its_rerun`: retire it, or rebind it to a fixture sub-cell.
  - `mutants.py`: rebind `sub_cell_is_a_pass`/`sub_cell_without_its_lane` to that fixture, and change `st_pass_held_is_failed`'s invariant text from "(NoLive, WR-C5-LIVE)" to "(nothing assigned, R244)".
  - Rerun: `make lab-rollout` / `runner.py --only k09`.

```diff
--- a/tests/integration/lab_rollout/scenarios_recover.py	2026-09-29 15:20:22.016770137 +0000
+++ b/tests/integration/lab_rollout/scenarios_recover.py	2026-09-29 15:20:38.332887107 +0000
@@ -429,6 +429,11 @@
 FIRST_PASS_S = 30.0
 
 
+def lab_workers_pass_s() -> float:
+    from infrx.lab.workers.__main__ import ROLLOUT_PASS_S
+    return ROLLOUT_PASS_S
+
+
 def free_port() -> int:
     """A spare port of the e8l block for the worker's loopback health listener."""
     import socket
@@ -445,8 +450,9 @@
     infrx.lab.workers rollout` (WR-R2-3, nothing injected: LAB_S3_BUCKET + LAB_OPERATOR_ID)
     reads the plan stored beside the release (`lab/<p>/releases/<policy_id>/plan.json`, D9's
     digest) and converges the alias to the baseline on its FIRST pass, with no second
-    decision. A running release beside it is held, never evaluated: R1's aggregates are
-    unreadable (NoLive) - the breach half waits on WR-C5-LIVE (evidence, not this case)."""
+    decision. A running release beside it with nothing assigned is held on that pass, never
+    evaluated on zeros; once a failed terminal job is assigned to its candidate, a later pass
+    sees the breach in D9's Live (0054, R244; WR-LIVE-K09) and rolls it back once."""
     import contextlib
     import os
     import signal
@@ -485,7 +491,7 @@
         assert decided == [("rollback", "rolled_back", lw.CONTROLLER, ["latency"])]
         assert lab.listing()[1] == promoted, "the killed controller's alias left the candidate"
         store_plan(policy.policy_id)
-        # a running release beside it (one live per endpoint): R1's aggregates are unreadable
+        # a running release beside it (one live per endpoint): nothing assigned yet - held
         held, held_ref = lab.launch(lab.policy(weights=(5_000,), candidates=(lab.CAND,)),
                                     lw.plan())
         store_plan(held.policy_id)
@@ -505,6 +511,18 @@
                 converged_after = round(time.monotonic() - began, 2)
                 break
             time.sleep(0.2)
+        first = (run(lab.releases().release(held_ref)).state, lab.decisions(held_ref))
+        # WR-LIVE-K09 (R244): the breach - one failed terminal job assigned to its candidate
+        from tests.d.test_code_mutants_live import job
+        job(lab.conn, held_ref, {"policy_id": held.policy_id}, lw.uid(1, 0x9b), lab.CAND,
+            "failed")
+        breached_after = None
+        while time.monotonic() - began < FIRST_PASS_S + lab_workers_pass_s() + 15 \
+                and process.poll() is None:
+            if lab.decisions(held_ref):
+                breached_after = round(time.monotonic() - began, 2)
+                break
+            time.sleep(0.5)
         exited_early = process.poll()
         process.send_signal(signal.SIGTERM)
         stopped = process.wait(30)
@@ -513,7 +531,8 @@
             "listing_before": listed, "promoted": promoted, "policy_candidate": candidate,
             "listing_after": lab.listing(), "converged_after_s": converged_after,
             "exited_early": exited_early, "exit_on_sigterm": stopped,
-            "decisions": lab.decisions(ref), "held": {
+            "decisions": lab.decisions(ref), "held_first_pass": first,
+            "breached_after_s": breached_after, "held": {
                 "state": run(lab.releases().release(held_ref)).state,
                 "decisions": lab.decisions(held_ref)}})
         text = (workdir / "rollout.log").read_text(errors="replace")
@@ -523,8 +542,11 @@
             f"the alias did not converge within the first pass ({FIRST_PASS_S}s): "
             f"{lab.listing()} is not the baseline {listed}: {tail}")
         assert lab.decisions(ref) == decided, "the pass recorded a second decision"
-        assert run(lab.releases().release(held_ref)).state == "running" and \
-            lab.decisions(held_ref) == [], "a running release was evaluated without R1's data"
+        assert first == ("running", []), f"nothing assigned was evaluated, not held: {first}"
+        assert run(lab.releases().release(held_ref)).state == "rolled_back" and \
+            lab.decisions(held_ref) == [("rollback", "rolled_back", lw.CONTROLLER,
+                                         ["error_rate"])], \
+            f"the pass did not roll back the breach in D9's Live once: {lab.decisions(held_ref)}"
         assert stopped == 0, f"SIGTERM did not stop the role cleanly ({stopped}): {tail}"
         assert "rollout pass failed" not in text, text[-3000:]
     finally:
```

- **WR-LIVE-DECIDE (composition / worker main outside the pass):** `rollout decide --approve` of an expansion still refuses by name ("WR-C5-LIVE"). Under R240 it may now be approved on R2's `expand` verdict over `PgReleaseStore.live` (plus `release_report`). This needs one design choice: `Controller.approve` does its own D9 transition, while 0043's `lab_decide_release_proposal` is a second CAS at the same fence. Either (a) evaluate first and then decide through 0043 with R2's `lab.rollout_decision.1` (`decision: expand`, the verdict's evidence refs), never calling `approve`; or (b) a ruling. Proposal: (a). The docstring and refusal text in `decide_proposal` also name the retired WR-C5-LIVE.
- **WR-LIVE-PAGE (G, `infrx/gateway/pilot.py` LabReleases records):** `/lab/v1/releases` `progress` is still null. It can be the running release's `PgReleaseStore.live(policy_ref)`, with null only while it is `None`. Not composed here (composition root).
- Makefile: applied in this lane as owned (`api-mutants` += 0054's list). No `lab-compositions` change was needed.

## Ruling proposals (unnumbered; R244 is the spec)

- **The release's budget is spent by its candidate arm.** R2's `Live.spent` is the candidate arm's settled spend: the traffic the release adds. The baseline's spend is read per arm by 0054, but it is not charged to the release's budget.
- **Health "ready" = `ready_private` or `active`.** 0007's deployment states have no literal `ready`. Every candidate must be ready, and a ref naming no deployment is unhealthy.
- **A unit refusal is counted `failed`, not `held`.** A plan whose budget unit differs from the jobs' settled unit (or from legacy USD) is a misconfiguration R2 refuses on every pass: it is logged and never decided.

## Open issues

- The DB clock is frozen in the D worlds, so `metrics_stale` always joins a PG hold. An `expand` verdict over real Live is proved at unit level (`test_control.py`), not on PG.
- R1 records assignments only while `rollout_routing` is ON (P-12, OFF). In production Live is therefore `[]`, and every running release stays held, as before.

## Estimate (remaining, this task)

- optimistic 1 h / likely 2 h / pessimistic 5 h; confidence medium.
- Basis: one verify/fix round (47-234 min per session-03) on a finished lane. WR-LIVE-K09 is lab-rollout-5's (~1 h plus a k09 rerun). WR-LIVE-DECIDE/PAGE are composition work (~0.5 day), outside this estimate.
