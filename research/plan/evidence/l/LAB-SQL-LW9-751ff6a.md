# LAB-SQL-LW9: WR-LW7-3a + WR-C7-TALLY + WR-LW8-3 (task L3, lab-sql)

Lane `lab-sql-lw9` (wave LW6), branch `codex/w5-lab-sql-lw9`, worktree
`.claude/worktrees/codex-w5-lab-sql-lw9`, base `33547abd`, code head `751ff6a2`. Opus implementer.
Tasklocal key `l3` (PG 57502, `infrx-l3-postgres`) for every real-PG run, `make api-test` and E4.
Nothing hosted, no box, no AWS/SSM/S3/Vercel, no secrets; no App/Lab file changed; every Lab
switch still OFF (no composition root's switches touched; `pilot.ReleaseRecords` sits behind
`LAB_RELEASES`, off).

## Commits (one per step)

| step | commit | what |
|---|---|---|
| 1 | `ffd64ee2` | `0058_lab_variant_identities.sql` (LOCAL-ONLY), `tests/l3sql/test_lw9.py` (4 checks), `tests/d/test_code_mutants_lw9.py` (21 SQL mutants), the harness pin, the Makefile line, `tests/d/test_code_mutants_lw8.py` GRANTED (WR-LW8-3) |
| 2 | `751ff6a2` | the ports: `PgLabVariants.variants` (0058's listing) + `.put_identities`; `PgReleaseStore.tally`; R3 `store(..., identities=, variants=)`; `pilot.ReleaseRecords` `progress.assignments`; units + Python/page mutants; composed-route PG case |

Changed paths (19): `Makefile` (one comment + one line), `apps/app/supabase/migrations/0058_lab_variant_identities.sql`,
`apps/infrx-api/infrx/state/{lab_variants.py, lab_rollout.py (tally only)}`,
`apps/infrx-api/infrx/rollouts/optimization/__init__.py` (`store` only),
`apps/infrx-api/infrx/gateway/pilot.py` (`_progress` + the one `progress` line + the docstring's tally sentence),
`apps/infrx-api/tests/d/{test_code_mutants_lw9.py (new), test_code_mutants_lw8.py (GRANTED only)}`,
`apps/infrx-api/tests/l3sql/{test_lw9.py, test_lw9_units.py, test_lw9_routes_pg.py (new), test_lw7.py, test_lw7_units.py, mutants.py, test_mutants.py}`,
`apps/infrx-api/tests/g/lab_releases/{test_lab_releases.py, test_lab_releases_composition_pg.py, mutants.py}`,
`tests/integration/test_harness.py` (pin), `research/plan/evidence/l/LAB-SQL-LW9-*`, the update JSON.

## What 0058 does

- **WR-LW7-3a.** Table `lab_variant_identities` (one row per `lab:variant` record, FK to
  `lab_records`; immutable, RLS on, browsers revoked, service_role select). It holds R3's base
  and variant `Identity` exactly as `model_dump(mode="json")` writes it. Check
  `lab_identity_shaped`: engine, engine_version, hardware and quantization are strings, and
  capabilities is an array of strings (what the Lab's `http.ts` `IDENTITY` reads). A missing
  field fails the check, never passes as NULL.
  - `lab_put_variant_identities {provider_org_id, variant_ref, base, variant, actor}`.
    Another provider's variant, a non-variant record or an unknown ref: `not_found` (R227).
    An unreadable shape or no actor: `invalid_request`. The same pair again is the same
    answer; a different pair is `state_conflict`.
  - `lab_optimization_variant_listing {provider_org_id}` is 0055's `lab_optimization_variants`
    in its order (`with ordinality`), each row plus `base`/`variant` (null until stored).
- **WR-C7-TALLY.** `lab_release_tally {policy_ref}` returns `[{serving_ref, pinned_by,
  requests}]` over the revision's `lab_rollout_assignments` joined to terminal jobs (0054's
  `requests`: succeeded, failed, cancelled, expired), ordered by serving then pin. The rows
  sum to 0054's two arms (asserted). No terminal job, or an unknown revision, is `[]`.
- **R251 grants (same file):** `infrx_lab_control` executes the listing, the tally and
  **0054's `lab_release_live`**. It never executes R3's write.

## The ports

- `PgLabVariants.variants` reads `lab_optimization_variant_listing`. `put_identities(ref,
  base=, variant=, provider_org_id=, actor=)` writes through 0058.
- `PgReleaseStore.tally(policy_ref)` reads `lab_release_tally`.
- R3's `store(data, variant, comparison, report, *, provider_org_id, actor, identities=None,
  variants=None)`: with `identities=(base, variant)`, it refuses identities that do not
  re-derive to the variant's two serving refs, before anything is written. It then writes
  both identities through `variants.put_identities` right after publishing the variant
  record, before the report and the comparison. Existing callers (tests only; there is no
  production caller of `store` yet) are unchanged.
- **`pilot.ReleaseRecords` (the exact lines):**
  - `_progress(live)` becomes `_progress(live, assignments)`, and its return's
    `"assignments": []` becomes `"assignments": assignments`.
  - In `releases()`, `"progress": _progress(await self.d9.live(item.policy_ref)),` becomes
    `"progress": _progress(live := await self.d9.live(item.policy_ref), None if live is None
    else await self.d9.tally(item.policy_ref)),` over three lines. The tally is read only once
    Live observed something; both read terminal jobs, so an unread tally would be `[]`.
  - Two docstring sentences name the tally.
  - Nothing else in `ReleaseRecords` changed (lab-rollout-7 and lab-c7-gaps edit its other
    parts in parallel).

## Fail-first (recorded before each implementation)

| seam | red | green |
|---|---|---|
| `INFRX_D_TASK=l3 pytest tests/l3sql/test_lw9.py` (no 0058, no ports) | **4 failed**: `42883 function infrx.lab_optimization_variant_listing(unknown) does not exist`; `lab_put_variant_identities(jsonb) does not exist`; `lab_release_tally(jsonb) does not exist`; `'PgLabVariants' object has no attribute 'put_identities'` | 4 passed |
| after 0058, before the ports | 1 failed (`put_identities`), 3 passed | 4 passed |
| `tests/g/lab_releases/test_lab_releases.py -k progress` over the base's `pilot.py` (the file swapped back from `HEAD`, then restored) | **1 failed**: `assignments` `[]` != the tally | 14 passed |

Note: on the base `33547abd`, that progress case was already red for another reason: it called
`pilot.ReleaseRecords(D9(), D7(), objects)` with 3 arguments after WR-LW7-1 made it 4. The case
now passes `None` as the variants port.

## Commands (exit codes, counts), cwd `apps/infrx-api` unless noted

| # | command | exit | result |
|---|---|---|---|
| 1 | `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql tests/g/lab_releases tests/r/optimization` | 0 | **62 passed, 3 skipped** (the skips are the r2-gated `test_lab_releases_composition_pg.py`, `test_lab_releases_pg.py`, `test_optimization_pg.py`) |
| 2 | `INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_code_mutants_lw9.py` (the new Makefile line) | 0 | **24 passed**: 21 SQL mutants killed, 0 survivors, plus 3 list guards (well-formed, not superseded, every case covered) |
| 3 | `INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_code_mutants_lw7.py tests/l3sql/test_mutants.py` (lw7's Makefile line) | 0 | **40 passed**: lw7's 23 SQL mutants still killed over the new port; the l3sql Python list has 12 mutants (8 new), 0 survivors |
| 4 | `INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_code_mutants_lw8.py tests/d/test_code_mutants_live.py` | 0 | **47 passed**: GRANTED, 0056's list and 0054's list all green with 0058 applied |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_releases/test_mutants.py` | 0 | **40 passed** (37 page mutants incl. 3 new tally mutants and 1 re-cut, + 3 guards; 0 survivors) |
| 6 | root: `apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_harness.py -k migration_set` | 0 | 1 passed (0058 pinned after 0056; 0057 slots in at merge) |
| 7 | `uv run --frozen ruff check tests/l3sql tests/g/lab_releases infrx/state infrx/rollouts/optimization infrx/gateway/pilot.py tests/d/test_code_mutants_lw9.py tests/d/test_code_mutants_lw8.py` | 0 | clean |
| 8 | root: `INFRX_D_TASK=l3 make api-test` | 2 | **6940 passed, 1 failed, 182 skipped, 10 xfailed** (1:48:06). The failure is `tests/d/test_upgrade_lab.py::test_lab_upgrade_preserves_history_money_identity_and_grants`, "Extra items: 'infrx.lab_variant_identities'": the upgrade proof's `NEW_TABLES` registry is not this lane's file. See WR-LW9-0 |
| 8b | WR-LW9-0 applied, then reverted: `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_upgrade_lab.py` | 0 | **7 passed** (so api-test has 0 failures once the one registry line lands) |
| 9 | E4, every switch OFF: `INFRX_D_TASK=l3 uv run --frozen pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | 0 | **2832 passed, 29 skipped, 0 failed** (20:02). The last recorded figure is COMPOSITION-7's 2831/0 (on p3); this run adds one, and the +1 is not decomposed. The skips are key- or stack-scoped, as in COMPOSITION-7 |

Containers: only `infrx-l3-postgres` (this lane's harness) was used. Logs are in
`scratchpad/lw9-sql/`.

## Mutants

- **SQL (`tests/d/test_code_mutants_lw9.py`, 21, all killed on l3):**
  - The three the brief requires:
    - A dropped grant: `lw9_tally_ungranted`, `_live_ungranted`, `_listing_ungranted`.
    - A foreign provider's identities: `lw9_identities_any_provider`.
    - A wrong tally: `lw9_tally_wrong_count`, `_queued_counted`, `_any_revision`, `_pin_lost`.
  - The rest:
    - Grants and browser access: `lw9_write_granted_to_unit`, `_listing_browser`.
    - Identity writes: `_identities_any_record`, `_identities_not_idempotent`,
      `_identities_overwrite_answered`, `_rows_mutable`.
    - Identity shape: `_shape_unchecked`, `_shape_missing_passes`, `_shape_capabilities_any`,
      `_actor_unnamed`.
    - The listing: `_listing_any_identities`, `_listing_swapped`, `_listing_ref_order`.
  - `check_the_stores_compose` is covered by the Python list, as lw7's was.
- **Python (`tests/l3sql/mutants.py`, 12 total, 8 new):**
  - New: `lw9_variants_unidentified`, `lw9_identities_swapped`, `lw9_identities_other_provider`,
    `lw9_tally_other_revision`, `lw9_tally_dropped`, `lw9_r3_identities_unchecked`,
    `lw9_r3_identities_unwritten`, `lw9_r3_identities_swapped`.
  - Re-cut: lw7's `lw7_variants_dropped` anchor, since the file now has two `_call`s.
    `lw7_variants_*` name the lw9 variants case, because lw7's variants unit case was
    replaced by lw9's (same oracle plus the function name).
- **Page (`tests/g/lab_releases/mutants.py`):**
  - `page_tally_invented` is replaced by `page_tally_dropped`, `page_tally_of_another` and
    `page_tally_unobserved`.
  - `page_progress_withheld` is re-cut onto the new line.

## Deviations

- **0055 is not redefined.** 0055's `lab_optimization_variants` keeps its body; 0058 adds
  `lab_optimization_variant_listing` over it. A `create or replace` in 0058 would make every
  lw7 variants mutant "superseded" (`migration_mutants.superseded`), and
  `tests/d/test_code_mutants_lw7.py` is not this lane's file. The route's port reads the new
  listing, so "0055's listing returns them" holds at the port.
- **0058 also grants `lab_release_live` to `infrx_lab_control`.** This is an R251 gap:
  composition-7's WR-LIVE-PAGE made `/lab/v1/releases` call 0054's Live for every release,
  but 0054 granted service_role only. Without this grant the unit's page is 42501 → 503
  once a release is listed, and the tally could never be reached there.
- **No `make lab-sql` target exists.** The equivalent was run instead: `tests/l3sql` plus
  the three SQL lists and the Python list on l3.
- The r2-gated `tests/g/lab_releases/test_lab_releases_composition_pg.py` expectation is
  updated (`assignments` = one cohort request on the candidate), but it is **NOT RUN** here:
  r2 is not this lane's key. The same composed read is proven on l3 by
  `tests/l3sql/test_lw9_routes_pg.py`.

## Wiring requests / proposed texts

- **WR-LW9-0 (tests/d/test_upgrade_lab.py; needed for `make api-test` green).** 0058 adds
  one Lab table, so the upgrade proof's `NEW_TABLES` gains `"infrx.lab_variant_identities"`
  (# 0058). Exact patch: `research/plan/evidence/l/LAB-SQL-LW9-wiring-upgrade.patch`. It was
  verified applied (row 8b: 7 passed) and then reverted.
- **WR-LW9-1 (apps/lab, proposed; not this lane's).** Re-cut `http.ts` `VARIANT` back to the
  required identities (`base: IDENTITY, variant: IDENTITY`; drop `MAYBE_IDENTITY`) and
  `R4-X113` back to "identities optional is a mutant", killed by `R4-H06` inverted. Do this
  only once 0058 is hosted **and** every stored variant has identities, i.e. every R3 `store`
  caller passes `identities=` (there is none in production yet). Until then R252 (b)
  stands: a pre-0058 variant legitimately reads null.
- **WR-LW9-2 (coordinator, l4).** The lab_control PG matrix is not rerun here. Run it on l4:
  `INFRX_D_TASK=l4 uv run --frozen pytest -q -m pg tests/i/lab_control` and
  `INFRX_MUTANTS=all INFRX_LAB_API_PG=1 INFRX_D_TASK=l4 uv run --frozen pytest -q tests/i/lab_control/test_mutants.py`.
  Expected: `present_records_answer_alike_on_both_logins` seeds a running release with the
  launcher's plan stored (line 101), so since composition-7 its lab-login `releases` probe
  reaches 0054's `lab_release_live`. That was a 42501 → 503 on the tip, and 0058's grant makes
  it 200 like the owner's. Nothing is assigned, so the tally is not read there. By reading
  only: not run here. The grant is pinned by `test_code_mutants_lw8.py` (HOLDS) and
  `test_lw9.py`'s ROLES check (`lw9_live_ungranted` killed).
- **WR-LW9-3 (coordinator, r2).**
  `INFRX_D_TASK=r2 uv run --frozen pytest -q tests/g/lab_releases/test_lab_releases_composition_pg.py`
  (the updated tally expectation).
- **WR-LW9-4 (whoever next composes R3).** An R3 CLI/worker calling `store` passes
  `identities=(base, variant), variants=PgLabVariants(connect)` on the Lab pool.
- **WR-LW9-5 (research, coordinator).** In COMPOSITION-7's open item "`assignments` is empty
  until a per-serving tally is read", and R-proposal 2's "`assignments` is empty until a
  per-serving tally is read, and is never invented", replace the text with "`assignments` is
  D9's per-(serving, pin) tally over terminal jobs (0058, WR-C7-TALLY)".

## Ruling proposal (unnumbered; rulings run through R253)

"R3 stores both revision identities of an optimization variant (engine, engine version,
hardware, quantization, capabilities, plus the rest of R3's `Identity`) once, beside its record,
at the variant's creation, and only identities that re-derive to its serving refs.
`/lab/v1/optimizations` returns them, null for a variant stored without them. The releases
page's `progress.assignments` is D9's per-(serving, pin) count of the revision's terminal
requests, summing to Live's two arms and read only once Live observed something. The control
login's route reads include 0054's Live (R251)."

## Open issues

- 0058 is LOCAL-ONLY. On the box, identities and the tally exist only after a hosted apply
  under R151/R201's three conditions. Until then the Lab keeps R252 (b).
- The number is 0058 by the brief (0057 is lab-capture-2's). Renumber at merge if 0057 moves.

## Estimate (remaining)

optimistic 0.5 h / likely 1.25 h / pessimistic 3 h, confidence medium. Basis: code, tests and
mutants are done with 0 survivors. What remains is one verify round (the lw9 list takes about
3 min and E4 about 22 min) plus the coordinator's WR-LW9-2/3 reruns on l4 and r2 (about
5 min each). WR-LW9-1 is Lab-lane work (about 1 h there) and is not counted. Analogue:
lab-sql-lw7 and lab-sql-lw8 at 0.5/1.5/4.
