# LAB-C7-GAPS (lane lab-c7-gaps, wave LW6; tasks R2 / I7) - head ed21f2a

- Base `33547abd`. Branch `codex/w5-lab-c7-gaps`, worktree `.claude/worktrees/codex-w5-lab-c7-gaps`.
- Keys: p3 only (PG 57530: E4, the new PG case, the Lab workers' / releases' PG cases). d1/55432, r2, l3, t2f, e5l, e8l, lab-on were not touched; b1 was not needed (nothing in B1 changed). No container was started or left; p3's `infrx-p3-postgres` was the harness's own.
- Every switch stays OFF. No migration, nothing hosted, no AWS/box/Vercel/secret access.

## Commits (tests first, one per step)

| commit | step | code | tests / docs |
|---|---|---|---|
| `5aef4aaf` | 0 base repair | - | `tests/g/lab_releases/test_lab_releases.py`: the WR-LIVE-PAGE case builds `ReleaseRecords` with #56's 4th argument (red on 33547abd: `TypeError: missing 'variants'`, so E4 on the tip was 1 failed) |
| `168853b4` | 1 C7-RV-1, C7-RV-2 | - | EXPAND case: a rollback verdict (`live(errors_=21)`) -> exit 1, `R2's verdict is rollback`, nothing decided; `started_at` = now -> exit 1, `before_horizon`, nothing decided. Mutants `lw_expand_on_rollback`, `lw_expand_started_at_replaced` |
| `e702a79a` | 2 C7-RV-4 | `lab_objects`: `settings(mode, env, (BUCKET,))` first (1 line + docstring) | DECIDE: `requires LAB_S3_BUCKET` in stderr, no `HeadBucket`; OBJECTS: an unset/blank bucket -> `RuntimeMisconfigured.missing == ("LAB_S3_BUCKET",)`, nothing connected. Mutant `lw_objects_bucket_unnamed` (cases OBJECTS, DECIDE) |
| `20dfaea0` | 3 C7-RV-5 | - | `infra/lab/app/lab.json` + README: `S3_MEDIA_BUCKET`, `S3_MEDIA_PREFIX` declared (lab-workers, rollout role); rollout `RUNBOOK.md` §3 note + log line |
| `910af683` | 4 C7-RV-6 | `gateway/pilot.py` `ReleaseRecords.releases` per-row fallback | fake case + p3 PG case; 5 fake mutants + 1 PG mutant; `page_progress_withheld` re-anchored |
| `ed21f2ad` | 5 WR-C7-DOC | - | 08 §5 "Lab worker roles" row only |

`pilot.py` lines touched (for lab-rollout-7, which edits the verdict in parallel): docstring of `ReleaseRecords` (+3 lines after "the verdict is D9's latest decision."); a `try: live, refused = await self.d9.live(item.policy_ref), None / except errors.InvalidRequest: live, refused = None, "unit_refused"` block inserted after `release, d = item.release, item.latest_decision`; `"progress": _progress(live),` (was `_progress(await self.d9.live(item.policy_ref))`); `if refused: out[-1].update(verdict=None, refused=refused)` after `out.append({...})`. The `"verdict": None if d is None else {...}` expression is unchanged (its anchors in tests/g/mutants.py:1155 and tests/integration/lab_rollout/mutants.py:378 still match).

## Fail-first (red, recorded before each implementation; `apps/infrx-api`)

| step | command | red |
|---|---|---|
| 0 | `uv run --frozen pytest -q tests/g/lab_releases/test_lab_releases.py` at 33547abd | 1 failed, 13 passed (`TypeError ... 'variants'`) |
| 1 | hand mutants on `__main__.py` (`!= "expand"` -> `== "hold"`; `started_at=release.started_at` -> `datetime.min.replace(tzinfo=timezone.utc)`), `-k expansion` | the OLD case passes under both (the gap); the NEW case fails under both (`AssertionError: (0, '')`, decided). The code already refused both: tests only |
| 2 | `pytest -q tests/w/test_lab_workers.py -k "decides_a_lab or lab_objects"` | 2 failed: stderr `LAB_S3_BUCKET did not answer HeadBucket (KeyError)`; `{"LAB_S3_BUCKET": ""}` connected (`Store`) |
| 4 | `pytest -q tests/g/lab_releases/test_lab_releases.py -k unit_refused` | 1 failed: `InvalidRequest: ... settled in USD` escaped the listing |
| 4 | `INFRX_D_TASK=p3 pytest -q tests/g/lab_releases/test_lab_releases_unit_refused_pg.py` against the old pilot.py | 1 failed: `InvalidRequest: this release's jobs settled in USD, no Lab unit: never converted` (real 0054) |
| WR | `pytest -q tests/i/lab_pipeline/test_preflight.py -k plan_location` (WR-C7G-PREFLIGHT test, preflight unpatched) | 1 failed: `LAB_S3_PREFIX: not a rollout setting` (+2) |

## Checks (green; exit 0)

| # | command | head | result |
|---|---|---|---|
| 1 | `uv run --frozen pytest -q tests/w/test_lab_workers.py tests/g/lab_releases/test_lab_releases.py` | ed21f2ad | 34 + 15 passed |
| 2 | `INFRX_MUTANTS=all .venv/bin/python -m pytest -q -p no:cacheprovider tests/w/test_lab_workers_mutants.py` | ed21f2ad | **158 passed, 0 survivors** (154 mutants: 151 + `lw_expand_on_rollback`, `lw_expand_started_at_replaced`, `lw_objects_bucket_unnamed`; 4 list checks) |
| 3 | `INFRX_MUTANTS=all .venv/bin/python -m pytest -q -rs -p no:cacheprovider tests/g/lab_releases/test_mutants.py` | ed21f2ad | **44 passed, 1 skipped, 0 survivors** (5 new `page_unit_*` mutants; the skip is the opt-in PG half) |
| 4 | `INFRX_MUTANTS=all INFRX_LAB_RELEASES_PG=1 INFRX_D_TASK=p3 ... tests/g/lab_releases/test_mutants.py -k "pg or well_formed or every_case"` | 910af683 | 4 passed: `page_unit_refusal_fails_listing_pg` killed on real 0054 (own pristine baseline) |
| 5 | `INFRX_D_TASK=p3 ... pytest -q -rs` on the r2-gated `test_lab_workers_decide_pg.py`, `test_lab_releases_pg.py`, `test_lab_releases_composition_pg.py` (throwaway copies with the gate set to p3, deleted after; the files are unchanged) + `test_lab_releases_unit_refused_pg.py` | ed21f2ad | 4 passed, 0 skipped |
| 6 | **E4, every switch OFF:** `INFRX_D_TASK=p3 .venv/bin/python -m pytest -q -rs -p no:cacheprovider tests/g tests/w tests/contracts tests/i/test_packaging.py` | ed21f2ad | **2835 passed, 30 skipped, 0 failed** (31m14s, concurrent with rows 2-3; floor >= 2831). +4 passed = the step-0 repaired case, the unit-refused fake case, the new p3 PG case, `test_every_pg_case_is_covered_by_a_pg_mutant`; +1 skip = the empty PG mutant parameter set |
| 7 | `.venv/bin/python -m pytest -q tests/integration/lab_*/test_mutants.py -k "well_formed or every_case or anchor or declared or known"` (repo root) | ed21f2ad | 19 passed: every Lab gate anchor intact (E8L's `st_decide_*`, K10_PORT's verdict line) |
| 8 | `pytest -q tests/i/lab_rollout tests/i/lab_pipeline tests/i/lab_control tests/i/lab_eval` | ed21f2ad | 87 passed, 6 skipped |
| 9 | `INFRX_D_TASK=l4 pytest -q tests/i/lab -k "declared or manifest or names"` (lab.json changed; file reads only, no l4 container used) | 20dfaea0 | 4 passed |
| 10 | `INFRX_D_TASK=p3 pytest -q tests/r tests/i/lab_rollout` | ed21f2ad | 86 passed, 12 skipped |
| 11 | `uv run --frozen ruff check` on every changed .py | ed21f2ad | All checks passed |

- `make api-test` was **not run**: it is `pytest -q` with the default key d1 (55432), which this lane must not touch. Rows 6-10 cover every track that imports the changed code (G, W, contracts, R, I-Lab, the Lab gates' anchors).

## Wiring requests (none applied)

- **WR-C7G-PREFLIGHT (coordinator; `infra/lab/workers/training/preflight.py` + `tests/i/lab_pipeline/{test_preflight.py,mutants.py}`):** the preflight refuses any name off the role's list, and `LAB_S3_PREFIX`, `S3_MEDIA_BUCKET`, `S3_MEDIA_PREFIX` are on none, so on the box R249's start-time refusal (C7-RV-5) cannot fire: the unit refuses the env file first (`not a rollout setting`). Patch (applies with `git apply --check` at ed21f2ad): `research/plan/evidence/coordinator/LAB-C7-GAPS-WR-C7G-PREFLIGHT.patch` - `allowed_names("rollout") |= {"LAB_S3_PREFIX", "S3_MEDIA_BUCKET", "S3_MEDIA_PREFIX"}`; case `test_i6_only_the_rollout_role_names_the_gateways_plan_location` (training/annotation still refuse `S3_MEDIA_BUCKET`); mutant `i6_pf_plan_location_refused`. Verified in this worktree then reverted: `test_preflight.py` 34 passed; `INFRX_MUTANTS=all tests/i/lab_pipeline/test_mutants.py -k "well_formed or every_case or anchor or i6_pf_plan_location or i6_pf_operator"` 5 passed. Note: `LAB_S3_PREFIX` is declared in lab.json for every worker role but on no preflight list, so annotation/training refuse it too (not widened here; say if they should).
- **Makefile (optional):** the p3 PG half of `tests/g/lab_releases/test_mutants.py` is opt-in (`INFRX_LAB_RELEASES_PG=1 INFRX_D_TASK=p3`), because a whole-suite parent holds the key's port lock; add it to a PG mutant target if one exists.

## Ruling proposals (unnumbered; rulings on the tip run through R253)

1. **(C7-RV-6, COORDINATOR DECISION) A unit-refused release degrades only its own row.** On `/lab/v1/releases`, a release whose Live D9 refuses by unit (R248: jobs settled in legacy USD, never converted) is listed with `progress: null`, `verdict: null` and `refused: "unit_refused"`; every other release lists with its own Live and verdict. Only that refusal (`InvalidRequest` from `PgReleaseStore.live`) degrades a row; any other failure of Live still fails the listing (a typed 503), never a guessed row. The `refused` key is present only on such a row (the port's `obj` check ignores extra keys, so the Lab page still parses the listing). The pass keeps R248 (counted failed, never decided).
2. **(C7-RV-4) A command that reads the Lab objects names an unset `LAB_S3_BUCKET` before anything.** `lab_objects` refuses an unset/blank `LAB_S3_BUCKET` as a missing setting (exit 2, `requires LAB_S3_BUCKET`), never as a HeadBucket failure; this covers `rollout decide --approve` of an expansion, which reads no role's NEEDS.

## Open issues

- R249's refusal fires on the box only after WR-C7G-PREFLIGHT; the runbook §3 note says to leave the names out of the unit's file until then and compare the two locations by hand.
- C7-RV-1/2 were test gaps only: the code already refused both; no behaviour changed.
- The step-0 red means E4 on 33547abd itself was 2830/1 failed (the #56 arity change landed after composition-7's E4).
- `test_lab_workers_decide_pg.py` / the releases PG cases stay gated on r2 (not edited); they ran green on p3 through throwaway copies (row 5).

## Estimate (remaining, this lane)

- optimistic 0.5 h / likely 1 h / pessimistic 3 h; confidence medium.
- Basis: one verify/fix round on a finished, test-only-heavy lane (47-234 min per session-03); WR-C7G-PREFLIGHT is a coordinator wiring (~0.3 h + the lab_pipeline mutant list).

- 2026-09-29: lab-c7-gaps lane evidence (C7-RV-1/2/4/5/6, WR-C7-DOC); E4 2835/0 on p3; local only.
