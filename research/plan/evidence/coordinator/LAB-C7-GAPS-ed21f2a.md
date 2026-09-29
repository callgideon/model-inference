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

`pilot.py` lines touched (for lab-rollout-7, which edits the verdict in parallel): docstring of `ReleaseRecords` (+3 lines after "the verdict is D9's latest decision."); a `try: live, refused = await self.d9.live(item.policy_ref), None / except errors.InvalidRequest: live, refused = None, "unit_refused"` block inserted after `release, d = item.release, item.latest_decision`; `"progress": _progress(live),` (was `_progress(await self.d9.live(item.policy_ref))`); `if refused: out[-1].update(verdict=None, refused=refused)` after `out.append({...})`. The `"verdict": None if d is None else {...}` expression is unchanged (its anchors in tests/g/mutants.py:1155 and tests/integration/lab_rollout/mutants.py:378 still match). **Corrected in the fix round (1-C7G-RV-A):** this edit does NOT leave the parallel lanes unaffected - it conflicts textually with both `codex/w5-lab-rollout-7` (e65bbecf) and `codex/w5-lab-sql-lw9` (751ff6a2) in `pilot.py`, `tests/g/lab_releases/mutants.py` and `tests/g/lab_releases/test_lab_releases.py`, and on meaning with lab-rollout-7 (its verdict reads the same `live`); see "Fix round" for the merge note. Onto `claude/consumer-v1` alone it merges clean.

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

## Fix round (1-C7G-RV-A)

The finding holds, and the conflicts are wider than it said: `git merge-tree --write-tree --name-only <lane> ffbf88c5` gives CONFLICT (content) in **three** files for each lane: `pilot.py`, `tests/g/lab_releases/mutants.py` **and** `tests/g/lab_releases/test_lab_releases.py`. `codex/w5-lab-rollout-7` is at e65bbecf and `codex/w5-lab-sql-lw9` at 751ff6a2 (local branches, no `origin/` refs here). Onto `claude/consumer-v1` (34e6ab91) the merge is clean.

**Merge note for the coordinator.** Whichever of lab-rollout-7 or lab-sql-lw9 lands after this lane, the loop in `ReleaseRecords.releases` keeps **one** guarded `live` read ahead of every use of it:

```python
            release = item.release                      # (rollout-7 drops `d`: `verdict` reads it)
            try:
                live, refused = await self.d9.live(item.policy_ref), None
            except errors.InvalidRequest:     # R248, C7-RV-6: this row only, typed
                live, refused = None, "unit_refused"
            out.append({
                ...
                "progress": _progress(live, None if live is None else          # lw9: no tally
                                      await self.d9.tally(item.policy_ref)),   # for a refused row
                "verdict": await self.verdict(provider_org_id, item, policy, full, live)})  # rollout-7
            if refused:
                out[-1].update(verdict=None, refused=refused)
```

- rollout-7's `release, live = item.release, await self.d9.live(...)` is **dropped**: that read is unguarded, and the R248 refusal would fail the listing again. Its `"verdict": await self.verdict(...)` line is kept **verbatim**, because its own mutant anchors on that exact line. `verdict()` with `live=None` does no read. It returns D9's decision (then nulled by the `update`) or None, so a refused row reads no B2 report.
- For lw9, the walrus `_progress(live := await self.d9.live(...), ...)` becomes `_progress(live, ...)`, and the tally is read only when `live is not None`, so a refused row never calls it. lw9's `page_tally_unobserved` anchor (`None if live is None else\n`) still matches.
- Re-anchors on the merged text: lw9's `page_progress_withheld` becomes `'"progress": _progress(live,'` → `'"progress": None and _progress(live,'`. With rollout-7 alone it stays `'"progress": _progress(live),'`. The `page_unit_*` anchors (`except errors.InvalidRequest:     # R248`, `live, refused = None, "unit_refused"`, `out[-1].update(verdict=None, refused=refused)`, `if refused:\n                out[-1].update(`) are unchanged under both resolutions.
- `test_lab_releases.py`: take the other lane's side, then append this lane's `test_lab_releases__a_unit_refused_live_nulls_its_own_row_and_the_others_list` unchanged. The two lanes' functions interleave in the conflict hunks, so the file cannot be resolved hunk by hunk. As of 2e5f93a8 its fake D9 already answers `tally` (0058) and asserts a refused row reads none, so the case needs no edit after lw9. `ReleaseRecords(D9(), D7(), objects, None)` still builds after rollout-7 (`reads=None` default; the listed row carries a D9 decision, so `verdict()` reads no report).
- The docstring combines both sentences: rollout-7's "the verdict is `verdict`'s (WR-LR6-VERDICT)" or lw9's WR-C7-TALLY `assignments` clause, plus this lane's R248 sentence.

**Verified by trial merges in this worktree.** Each trial ran `git merge --no-commit --no-ff <lane>`, resolved the files as above, ran the checks, and then ran `git merge --abort`. Nothing was committed from a trial, and the tree was clean after each.

| trial | command (apps/infrx-api) | result |
|---|---|---|
| + rollout-7 (e65bbecf) | `pytest -q tests/g/lab_releases/test_lab_releases.py` | 19 passed |
| + rollout-7 | `INFRX_MUTANTS=all ... tests/g/lab_releases/test_mutants.py` | **57 passed, 1 skipped, 0 survivors**: `page_unit_refusal_fails_listing` still kills UNIT_REFUSED, and rollout-7's verdict mutants all die |
| + rollout-7 | `INFRX_D_TASK=p3 ... test_lab_releases_unit_refused_pg.py`; `INFRX_MUTANTS=all INFRX_LAB_RELEASES_PG=1 INFRX_D_TASK=p3 ... test_mutants.py -k pg` | 1 passed; `page_unit_refusal_fails_listing_pg` PASSED (killed on real 0054) |
| + lw9 (751ff6a2) | `pytest -q tests/g/lab_releases/test_lab_releases.py` | 15 passed |
| + lw9 | `INFRX_MUTANTS=all ... tests/g/lab_releases/test_mutants.py` | **46 passed, 1 skipped, 0 survivors** (with the `page_progress_withheld` re-anchor) |
| + lw9 | the PG case and the PG mutant, as above | 1 passed; `page_unit_refusal_fails_listing_pg` PASSED |

A three-way trial (both lanes on top of this one) was not run, because it needs a committed intermediate merge. The block above is the union of the two verified resolutions.

| check at 2e5f93a8 | result |
|---|---|
| `pytest -q tests/g/lab_releases/test_lab_releases.py`; `ruff check` | 15 passed; clean |
| `INFRX_MUTANTS=all ... tests/g/lab_releases/test_mutants.py` | **44 passed, 1 skipped, 0 survivors** |

Only a test fake changed in this round, with no code change, so E4 was not rerun. The E4 at ed21f2ad (2835/0 on p3) stands.

- 2026-09-29: lab-c7-gaps lane evidence (C7-RV-1/2/4/5/6, WR-C7-DOC); E4 2835/0 on p3; local only.
- 2026-09-29: fix round 1-C7G-RV-A: corrected the pilot.py line (conflicts with lab-rollout-7 and lab-sql-lw9) and added the merge note, verified by two trial merges; the test fake answers tally; local only.

## Coordinator rulings

- **R255** (proposal 1, C7-RV-6): a release whose Live is refused as legacy USD (R248) degrades only its own row of `/lab/v1/releases`: `progress` and `verdict` null with `refused: "unit_refused"`; the other rows list; any other Live failure still fails the listing.
- **R256** (proposal 2, C7-RV-4/C7-RV-5): an unset `LAB_S3_BUCKET` is named first: the rollout role and `rollout decide` refuse with 'requires LAB_S3_BUCKET' before any bucket call; R249's same-location check reads the stripped values and fires on the box, because the rollout unit's preflight allows `S3_MEDIA_BUCKET` / `S3_MEDIA_PREFIX` / `LAB_S3_PREFIX`.
- Numbered in 08 §10 directly after R253 at the lab-c7-gaps merge on `codex/w5-merge-60` (R254 is on `codex/w5-merge-58`); next free R257. The coordinator sorts at integration.

## Applied at merge (codex/w5-merge-60, one wirings commit)

- **WR-C7G-PREFLIGHT**: `LAB-C7-GAPS-WR-C7G-PREFLIGHT.patch` applied as written (`allowed_names("rollout") |= {LAB_S3_PREFIX, S3_MEDIA_BUCKET, S3_MEDIA_PREFIX}`; case `test_i6_only_the_rollout_role_names_the_gateways_plan_location`; mutant `i6_pf_plan_location_refused`). 1-C7G-RV-B: rollout `RUNBOOK.md` §3 and `infra/lab/app/README.md` no longer say to leave the names out; the rollout unit's env names `S3_MEDIA_BUCKET` / `S3_MEDIA_PREFIX` equal to the gateway's (R249). 08 §5's row stays as the lane wrote it.
- **0-F1**: the unit-refused case also asserts that `errors.NotFound` from `d9.live` fails the listing, and that 0054's mixed-units `InvalidRequest` (CREDIT and USD) reads `unit_refused`; mutant `page_not_found_is_a_unit_refusal` (`except (errors.InvalidRequest, errors.NotFound):`).
- **0-F3**: `lab_objects` strips `LAB_S3_BUCKET` once (`bucket = settings(...)[BUCKET]`) and uses that value for the R249 comparison and the connection; case (`" media "` matches `S3_MEDIA_BUCKET=media` and connects to `media`) + mutant `lw_objects_bucket_unstripped`; `lw_objects_bucket_unnamed` re-anchored on the new line.
- **0-F4 / 1-C7G-RV-C**: apps/lab: the port's `Release` carries the optional `refused?: "unit_refused"`, the HTTP decoder accepts it (`opt(oneOf("unit_refused"))`), and the releases page's traffic reads 'progress unavailable: settled in another unit' for such a row, never 'no traffic observed'; cases in R4-V02 / R4-H01; mutant R4-X122.

## Integration note

- `pilot.py`'s `ReleaseRecords` is also edited by lab-rollout-7 (the verdict) and lab-sql-lw9 (assignments / tally); the coordinator resolves those conflicts at their merges, following the merge note in "Fix round" above.
- Step 0's repair of `tests/g/lab_releases`' WR-LIVE-PAGE case (`ReleaseRecords` takes 4 arguments after #56) was a tip bug: the tip's E4 was 1 failed; it is fixed here.
- 2026-09-29: coordinator rulings R255/R256 and the merge wirings (WR-C7G-PREFLIGHT, 0-F1, 0-F3, 0-F4) recorded at the lab-c7-gaps merge on codex/w5-merge-60; local only.
