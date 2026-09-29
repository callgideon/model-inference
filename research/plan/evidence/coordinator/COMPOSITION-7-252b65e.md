# COMPOSITION-7 (lane composition-7, wave LW6) - head 252b65e

- Base `6ee21d2f` (merge #52 on the tip after #53). Branch `codex/w5-composition-7`, worktree `.claude/worktrees/codex-w5-composition-7`.
- Commits (one per task, tests first):
  - `c85e3b4e` WR-LIVE-DECIDE
  - `6ff7045b` WR-LIVE-PAGE
  - `897be3e0` WR-LR5-3
  - `252b65e8` WR-LEM-R3
  - then this evidence and the update JSON.
- Keys: r2 (PG 57534) for the decide/page PG proofs; b1 (PG 57520, model-fake 57521) for B1's PG proof; p3 for E4. d1/55432, e8l, l3sql, l4, e5l and t2f were not touched. No container is left on these ports.
- Every Lab switch stays OFF. No migration was added or edited, and nothing was applied hosted. There was no AWS, box, Vercel or secret access.

## WR list

| WR | status | code | tests |
|---|---|---|---|
| WR-LIVE-DECIDE | done | `infrx/lab/workers/__main__.py`: new `expansion_verdict`; `decide_proposal` changed | `tests/w/test_lab_workers.py` (new case + the DECIDE case updated), `tests/w/test_lab_workers_decide_pg.py` (r2), `tests/w/lab_workers_mutants.py` |
| WR-LIVE-PAGE | done | `infrx/gateway/pilot.py`: `_progress` + `ReleaseRecords.releases` | `tests/g/lab_releases/test_lab_releases.py` (new case), `test_lab_releases_composition_pg.py` (r2, extended), `tests/g/lab_releases/mutants.py`; `tests/g/test_startup.py` fake (deviation, below) |
| WR-LR5-3 | done | `infrx/lab/workers/__main__.py` `lab_objects` (R249) | `tests/w/test_lab_workers.py` (new case), `tests/w/lab_workers_mutants.py` |
| WR-LEM-R3 | done | `infrx/evaluation/runner/__init__.py` (`_attempt` refusal name, `_finish` passes `error=`); `infrx/state/lab_data.py` unchanged (`finish(..., error=)` already existed) | `tests/b/runner/{test_runner.py, world.py, mutants.py, test_runner_pg.py}` |

### WR-LIVE-DECIDE

`rollout decide --approve` of an `expand` proposal now evaluates the release the same way the pass does:
- the plan stored beside the release is read through `lab_objects`; none stored is refused naming WR-C5-PLAN;
- D9's digest is checked (`the plan changed after launch`);
- `PgReleaseStore.live(policy_ref)`: None is refused as `held: no admitted request is assigned…`;
- `release_report(PgLabReads, PgLabDataStore, …)`;
- R2's `evaluate` at the wall clock.

Only an `expand` verdict is decided. It goes through 0043 (`PgReleaseProposals.decide`) with a `lab.rollout_decision.1` whose `decision` is the proposal's kind (`expand`) and whose `evidence_refs` are the verdict's. It never goes through `Controller.approve`, so there is one CAS at the proposal's fence. An approved expansion returns 0 and moves no alias.

Any other verdict is refused by name, for example `state_conflict: not approvable: R2's verdict is hold (min_requests, no_report, …)`. A `rollback` verdict is refused the same way (the pass rolls it back). A unit mismatch is R2's `invalid_request`. In every refusal case the exit is 1 and nothing is decided.

The docstrings (module and `decide_proposal`) no longer name WR-C5-LIVE. The policy is now resolved before 0043's decision, so a resolve failure is reported as "was not decided".

An expansion needs `LAB_S3_BUCKET` (the stored plan). Without it the command exits 2, through `lab_objects`' existing refusal.

### WR-LIVE-PAGE

For every shown release (running, approved, rolled back), `ReleaseRecords.releases` reads `self.d9.live(item.policy_ref)`. It is shown in port.ts's `Progress` shape (snake_case):
- `observed_until` (UTC `…Z`);
- `baseline` / `candidate` `{requests, errors, p99_ms}`;
- `quality_covered`;
- `spent {amount, unit}`, which is the candidate arm's spend (R246);
- `candidate_healthy`;
- `assignments: []`.

`progress` is null only while Live is None. The `assignments` list is empty and never invented, because no per-serving tally is readable (WR-C7-TALLY below). The page's `verdict` is still D9's latest decision (see the open issues).

### WR-LR5-3

This is the E8L § Fix round diff. The one change is `env.get(BUCKET)` in the comparison: a unit that names `S3_MEDIA_BUCKET` but not `LAB_S3_BUCKET` is refused by name instead of raising `KeyError`.
- A unit whose `S3_MEDIA_BUCKET`/`S3_MEDIA_PREFIX` (media bucket trimmed; unset prefix `infrx/`) differ from `LAB_S3_BUCKET`/`LAB_S3_PREFIX` (unset `infrx/`) raises `RuntimeMisconfigured` before any bucket is asked. The refusal names both settings and WR-C5-PLAN, so every role exits 2.
- The same location, or no `S3_MEDIA_BUCKET`, connects and probes as before.

### WR-LEM-R3

This is the E6L-J11 'Carried' diff, plus one correction.
- The runner's `DomainError` branch names `media_foreign` / `video_over_cap` as `invalid_request:<name>`. Any other refusal stays its bare code, so the j07 consumers are unchanged.
- `_finish` now passes `error=reason.lower() if reason else None` to D7.
- **Correction to the carried diff:** 0034 checks `error_code ~ '^[a-z][a-z0-9_:.-]{0,99}$'`. The runner's `error:<ExceptionName>` (for example `error:ZeroDivisionError`) would be refused (`invalid_request`) and would fail the delivery. The stored code is therefore lower-cased. The in-process report keeps `error:ZeroDivisionError`, and D7 stores `error:zerodivisionerror`.
  - The fake store now enforces 0034's check. Mutant `b1_error_code_cased` (dies_by `InvalidRequest`, documented) proves it.
  - The b1 PG run showed the same on real 0034. During development a bad fixture made every case `error:JSONDecodeError`, and 0034 accepted the lower-cased code.

## Fail-first (red, recorded before each implementation; `apps/infrx-api`)

| task | command | red result |
|---|---|---|
| DECIDE | `uv run --frozen pytest -q tests/w/test_lab_workers.py -k "decide or expansion"` | 2 failed, 1 passed. The new case's first refusal read `…an expansion is approved on R2's expand verdict over R1's aggregates (WR-C5-LIVE)` where it expected WR-C5-PLAN. The DECIDE case got exit 1 where it expected 2 (no bucket) |
| PAGE | `uv run --frozen pytest -q tests/g/lab_releases/test_lab_releases.py -k progress` | 1 failed: `each release's Live is read for its own revision`, `[] == [<both refs>]` (Live never read) |
| LR5-3 | `uv run --frozen pytest -q tests/w/test_lab_workers.py -k lab_objects` | 1 failed: `{'S3_MEDIA_BUCKET': 'other'}` connected (`Store`) instead of `RuntimeMisconfigured` |
| LEM-R3 | `uv run --frozen pytest -q tests/b/runner/test_runner.py -k "refused_clip or unexpected"` | 2 failed. The attempt errors were `[None, None, None]`, and the report had `{…001: 'invalid_request', …002: 'invalid_request'}` instead of the names |

## Checks (green; exit 0 unless stated)

| # | command | head | result |
|---|---|---|---|
| 1 | `uv run --frozen pytest -q tests/w/test_lab_workers.py` | 897be3e0 | 34 passed |
| 2 | `INFRX_MUTANTS=all .venv/bin/python -m pytest -q -p no:cacheprovider tests/w/test_lab_workers_mutants.py` | 897be3e0 | **155 passed, 0 survivors**. That is 151 mutants: 10 new `lw_expand_*`, 6 new `lw_objects_*`, `lw_decide_expand_without_live` re-texted with cases DECIDE+EXPAND, and `lw_rollout_empty_live_evaluated` re-anchored on `"no admitted"`, because `if current is None: raise DependencyUnavailable(` now occurs twice. The other 4 items are the list checks |
| 3 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_releases/test_mutants.py` | 6ff7045b | **38 passed, 0 survivors** (11 new `page_*` mutants on `gateway/pilot.py`; FILES += pilot.py) |
| 4 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/b/runner/test_mutants.py` | 252b65e8 | **100 passed, 0 survivors**. 4 new mutants (`b1_clip_refusal_unnamed`, `b1_clip_refusal_one_name`, `b1_refusal_not_stored`, `b1_error_code_cased`); `b1_refusal_unnamed` re-anchored on `else refused.code))` |
| 5 | `INFRX_D_TASK=r2 … pytest -q -rs tests/w/test_lab_workers_decide_pg.py tests/g/lab_releases/test_lab_releases_pg.py tests/g/lab_releases/test_lab_releases_composition_pg.py tests/r/control/test_control_pass_pg.py tests/r/control/test_control_pg.py` | 252b65e8 | 9 passed, 0 skipped |
| 6 | `INFRX_D_TASK=b1 … pytest -q -rs tests/b/runner/test_runner_pg.py tests/w/test_worker_lab_eval_pg.py` | 252b65e8 | 14 passed, 0 skipped |
| 7 | **E4, every switch OFF:** `INFRX_D_TASK=p3 .venv/bin/python -m pytest -q -rs -p no:cacheprovider tests/g tests/w tests/contracts tests/i/test_packaging.py` | 252b65e8 | **2831 passed, 29 skipped, 0 failed** (22m26s; the floor is ≥ 2830). The skips are key- and stack-scoped: the r2/b1/b3/p1/p2/j2 PG cases (the r2/b1 ones pass in rows 5-6), MinIO, t2i ClickHouse, the t2f stack and empty default mutant parameter sets. 2831+29 = 2860 = LAB-LIVE's 2830+26 + 3 new fake cases + 1 new PG case |
| 8 | `.venv/bin/python -m pytest -q tests/integration/lab_*/test_mutants.py -k "well_formed or every_case or anchor or declared or known"` (repo root) | 252b65e8 | 19 passed. Every Lab gate's mutant anchor (lab_evaluate 55, lab_improve 46, lab_local 36, lab_observe 70, lab_operate 50, lab_rollout 83) is still in the source as often as declared (script check, 0 moved). E8L's `st_decide_*` anchors on `workers/__main__.py` are intact: `if found["kind"] != "rollback":`, `if not approve:`, the `reasons=(…)` line and the `emergency_rollback` line |
| 9 | `pytest -q tests/i/lab tests/i/lab_rollout tests/i/lab_pipeline tests/b/runner/test_runner.py`; `tests/i/lab_control tests/i/lab_eval` | 252b65e8 | 118 passed, 1 xfailed; 27 passed, 5 skipped |
| 10 | `uv run --frozen ruff check` on every changed .py | 252b65e8 | All checks passed |

- `make api-test` (the whole suite) was not run. Rows 1-9 cover the affected tracks (G, W, B, R, I-Lab, contracts).

## Deviation (declared)

- **`tests/g/test_startup.py`** is not an owned path, but it has a 1-method, 1-docstring edit. `test_lab_releases__the_surface_is_d9_d7_the_stored_plan_and_0043s_proposals` builds `pilot.ReleaseRecords` over a D9 fake without `live`, and WR-LIVE-PAGE reads `live`. Without the edit, E4 fails 1 case. Its fake gains `async def live(self, policy_ref): return None`, and its assertion (`progress` null) is unchanged. The docstring's "R1's aggregates are not readable (WR-C5-LIVE)" becomes "Nothing assigned yet (D9's Live is None)". The coordinator may re-apply this as a wiring.

## Wiring requests (none applied)

- **WR-C7-K10 (lab-rollout-6, `tests/integration/lab_rollout/scenarios_route.py` + `mutants.py`):** k10's port half asserts `"WR-C5-LIVE" in widened.stderr` (line 430). With WR-LIVE-DECIDE, the refusal of an expansion over a release with nothing assigned is `the proposal was not decided: dependency_unavailable: … held: no admitted request is assigned to this release yet`, and the exit is still 1. Patch:
  ```diff
  -        # WR-LR5-RV3: an expansion's approval is refused while R1's aggregates are unreadable
  -        # (WR-C5-LIVE, R240) and a rejection moves nothing. The route files no expansion
  +        # WR-LR5-RV3: an expansion's approval is refused on R2's verdict over D9's Live - here
  +        # nothing is assigned: held (R240, WR-LIVE-DECIDE) - and a rejection moves nothing. The route files no expansion
  …
  -        assert widened.returncode == 1 and "WR-C5-LIVE" in widened.stderr, widened.stderr[-1500:]
  +        assert widened.returncode == 1 and "no admitted request is assigned" in widened.stderr, \
  +            widened.stderr[-1500:]
  ```
  - In `mutants.py`, `st_decide_expand_approved`'s invariant text becomes "an expansion's approval is refused without R2's expand verdict (R240): no D9 decision, the alias unchanged". Its anchor is unchanged and it is still killed by k10: with `if False`, the expansion is decided `expand` with no evidence, the contract refuses it, the process dies non-zero, and the stderr misses the held text.
  - The comment above it, "(WR-C5-LIVE, R240)", becomes "(R240, WR-LIVE-DECIDE)".
  - Rerun: `runner.py --only k10` plus the two `st_decide_*` stack mutants.
- **WR-C7-TALLY (lab-sql, local-only):** port.ts's `Progress.assignments` (`{serving_ref, pinned_by, requests}` per serving) has no read. 0054 aggregates per arm only. A sibling read, or a column on `lab_release_live` that groups `lab_rollout_assignments ⋈ jobs` by `(serving_ref, pinned_by)`, would let `pilot._progress` fill it. Until then the page shows `[]`.
- **WR-C7-DOC (coordinator, 08 §5 "Lab worker roles" row, research only):** replace "a running one is held until R1's aggregates are readable (WR-C5-LIVE; composition-5, WR-R2-3)" with "a running one is evaluated on D9's Live (0054, R244), held while nothing is assigned"; replace "an expansion refused until WR-C5-LIVE" with "an expansion approved only on R2's `expand` verdict over D9's Live and the release's B2 report (R240, WR-LIVE-DECIDE; needs LAB_S3_BUCKET for the stored plan)"; replace "(… the refusal at the worker's start with composition-7)" with "(… refused at the worker's start, composition-7)". Also note that `/lab/v1/releases` `progress` is D9's Live (WR-LIVE-PAGE).
- **E8L runner (lab-rollout-6):** the `k10-ui-composed` sub-cell's lane WR-C6-LIVE is now half-closed. `progress` is composed, but `verdict` is still D9's latest decision, not R2's latest `evaluate` (hold and expand are not persisted; see COMPOSITION-6 "R4-swap"). Re-judge the sub-cell with WR-LR5-1.

## Ruling proposals (unnumbered; rulings run through R249)

1. **An operator's expansion is decided on a fresh verdict, in one CAS.** `rollout decide --approve` of an `expand` proposal evaluates R2 at decide time, over the plan stored beside the release (D9's digest), D9's Live and its B2 report (R242). Only an `expand` verdict is decided, through 0043 at the proposal's fence, with R2's `lab.rollout_decision.1` `expand` carrying the verdict's evidence refs. `Controller.approve` is not called. A hold, a rollback verdict, a unit refusal, nothing assigned, or a missing plan refuses by name, and the proposal stays pending.
2. **The release page's progress is D9's Live.** `/lab/v1/releases` `progress` is `PgReleaseStore.live(policy_ref)` for every shown release, and null only while nothing is assigned. Its spend is the candidate arm's (R246). `assignments` is empty until a per-serving tally is read, and is never invented.
3. **A failed Lab eval attempt stores its reason as 0034's error code.** B1 passes every failed attempt's reason to D7, lower-cased to 0034's form. R239's two clip refusals are named `invalid_request:media_foreign` / `invalid_request:video_over_cap` in the run report and on the attempt row. Any other refusal keeps its bare code.

## Open issues

- The D worlds freeze the DB clock, so an `expand` over real Live cannot be reached on PG: `metrics_stale` always holds, as noted in LAB-LIVE. The r2 proof covers the two refusals over real Live (nothing assigned; a hold naming `min_requests`). It then injects R2's verdict once to prove the decision half on real 0043: one `expand` event by the operator with the evidence, release `approved`@2, proposal `approved`, and a second decide refused. The expand path itself is proven at unit level (the `lw_expand_*` mutants).
- A release whose jobs settled in legacy USD makes `PgReleaseStore.live` raise `InvalidRequest` (R248), and the page's whole listing then fails with that error. This is a misconfiguration R248 already refuses on the pass. If the page should degrade per row, that needs a ruling.
- R1 records assignments only while `rollout_routing` is ON (P-12, OFF), so in production `progress` stays null and every expansion approval is refused as held.

## Estimate (remaining, this lane)

- optimistic 1 h / likely 2 h / pessimistic 4 h; confidence medium.
- Basis: one verify/fix round on a finished lane (47-234 min per session-03). WR-C7-K10 is lab-rollout-6's (~0.5 h plus a k10 rerun). WR-C7-TALLY is a small lab-sql slice (~2-4 h, outside this estimate).

- 2026-09-29: composition-7 lane evidence (WR-LIVE-DECIDE, WR-LIVE-PAGE, WR-LR5-3, WR-LEM-R3); E4 2831/0 on p3; local only.
