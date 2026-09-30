# LAB-R3-IDENTITIES (lane lab-r3-identities, wave LW6) — WR-LW9-4 + WR-LW9-1 (tasks R3/L3 + the Lab app)

Base `6ca7879f` (merge #65 tip). Branch `codex/w5-lab-r3-identities`, worktree
`.claude/worktrees/codex-w5-lab-r3-identities`. Keys: `l3` (PG cases, 57502), `p3` (E4, 57530).
Commits: `c1bb290a` (step 1, Python), `cf9d30f1` (step 2, Lab), then this evidence commit.
Every Lab switch OFF throughout (no `LAB_*` / `INFRX_LAB*` variable set in the shell; defaults).

## What changed (18 paths: 15 product/test, 3 evidence)

- **Step 1 — WR-LW9-4 (R3 + pilot).** There is **no production caller** of
  `optimization.store` on the base: `grep -rn 'optimization.store|r3.store(|lab:variant'` over
  `infrx/` finds none (only tests and E8L's `tests/integration/lab_rollout/scenarios_parity.py`).
  So "every caller passes identities" is made true by construction, at the one function every
  caller routes through:
  - `infrx/rollouts/optimization/__init__.py` `store(..., *, provider_org_id, actor,
    identities: tuple[Identity, Identity], variants)`: both keywords are **required** (no
    default); `identities is None or variants is None` is refused `invalid_request` before
    anything is written (F3 widened); the serving-ref check and the `put_identities` write are
    unconditional. Module docstring R3.d names the identities.
  - `infrx/gateway/pilot.py` **lines 414–426** (new, nothing else in pilot touched; ReleaseRecords
    untouched): `lab_optimizations(connect)` = `functools.partial(optimization.store,
    PgLabDataStore(connect), variants=PgLabVariants(connect))` — R3's store composed on the Lab
    pool; the caller names `identities=(base, variant)`. Not mounted anywhere: nothing reachable
    from the launched App/API changes.
  - Tests: `tests/g/test_startup.py` (new anchor case), `tests/l3sql/test_lw9_units.py` (the store
    case refuses no identities and no port, nothing written), `tests/l3sql/test_lw9_routes_pg.py`
    (new PG case: store → 0058 → listing → route), `tests/r/optimization/test_optimization.py`
    (+`put_identities` on the fake; the call passes identities), `test_optimization_pg.py` (passes
    identities + `PgLabVariants`; gate also accepts `l3`).
- **Step 2 — WR-LW9-1 (apps/lab).** `http.ts` `VARIANT`: `base: nul(IDENTITY), variant:
  nul(IDENTITY)` — the keys are required, `MAYBE_IDENTITY` (opt) dropped; null stays readable for
  a variant stored before R3 recorded identities. `port.ts` `Variant.base/variant: Identity | null`
  (required). `view.ts` `scope`: null shows `identity not recorded (<serving ref>)`. The page
  (`app/(provider)/optimizations/page.tsx`, unchanged) renders `r.base`/`r.variant`, so it shows the
  identities, or the legacy text.

"Identities derived from the variant's and the source policy's serving refs": a serving ref is a
digest of the identity, so an identity cannot be derived from a ref. The composition takes the
pair the caller registered the variant from, and `store` (Python) and 0058 (SQL, F6) both refuse a
pair that does not re-derive to the variant's two serving refs. Nothing ties the base to a source
*policy* revision's serving ref today; not added (see open issues).

## Fail-first (recorded before each implementation)

| Seam test | Against the base | After |
|---|---|---|
| `pytest tests/g/test_startup.py -k lab_optimizations` | 1 failed: `AttributeError: module 'infrx.gateway.pilot' has no attribute 'lab_optimizations'` | 1 passed |
| `pytest tests/l3sql/test_lw9_units.py` | 1 failed, 3 passed: the store case (`variants=None` answered "identities are stored through the variants port"; `identities=None` published the variant and skipped identities) | 4 passed |
| `INFRX_D_TASK=l3 pytest tests/l3sql/test_lw9_routes_pg.py` | 1 failed, 1 passed: `AttributeError ... no attribute 'lab_optimizations'` | 2 passed |
| `node --test tests/r/http.test.ts tests/r/view.test.ts` (apps/lab) | 2 failed, 16 passed: R4-H06 (an answer omitting base/variant was read), R4-V12 (legacy null showed the bare serving ref) | 18 passed |

## Checks (exit codes, counts)

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `cd apps/infrx-api && uv run --frozen pytest -q tests/l3sql/test_lw9_units.py tests/r/optimization tests/g/test_startup.py` | 0 | 60 passed, 1 skipped (the r2-gated PG file without a key) |
| 2 | `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql/test_lw9_routes_pg.py tests/r/optimization/test_optimization_pg.py` | 0 | 3 passed (composed path store → 0058 → listing → route; R3's PG store with identities) |
| 3 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/l3sql/test_mutants.py` | 0 | 16 passed (14 mutants killed + well_formed + every_case) |
| 4 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/r/optimization/test_mutants.py` | 0 | 50 passed (48 killed + 2) |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/test_mutants.py -k 'well_formed or every_case or lab_optimizations or lab_releases_variants'` | 0 | 10 passed (5 new + 3 variants anchors killed + 2); full g list not rerun (anchor-count check below) |
| 6 | anchor-count script over every mutant list whose file is `gateway/pilot.py` or `rollouts/optimization` (g, g/lab_releases, t/capture, w x2, m x2, i) | 0 | every `old` occurs exactly its declared count after the edit |
| 7 | `uv run --frozen ruff check` (changed Python) | 0 | All checks passed |
| 8 | `make lab-typecheck`, `make lab-lint`, `make lab-test`, `make lab-build` | 0/0/0/0 | lab-test: 265 tests, 251 pass, 0 fail, 14 skipped (stack cases without a stack) |
| 9 | `cd apps/lab && node tests/r/run-mutants.mjs` | 0 | 31 cases all named; 126 mutants, 126 killed, 0 not killed |
| 10 | **E4, every switch OFF, p3:** `INFRX_D_TASK=p3 uv run --frozen pytest -q tests/g tests/w tests/contracts tests/i/test_packaging.py` | 0 | **2842 passed, 0 failed, 32 skipped** (22:27) (≥ 2834/0) |
| 11 | `INFRX_D_TASK=l3 make api-test` | 2 | **6977 passed, 1 failed, 194 skipped, 10 xfailed** (1:26:17). The one failure is `tests/i/lab_control/test_mutants.py::test_every_case_is_covered_by_a_mutant` (`test_control_routes_pg__an_assigned_running_release_reads_its_verdict_on_the_lab_login` named by no mutant): **pre-existing on the base** — this lane changes nothing under `tests/i` (`git diff 6ca7879f -- apps/infrx-api/tests/i` empty) and `pytest tests/i/lab_control/test_mutants.py -k every_case` fails identically on the untouched files; the case came with lab-rollout-7 (`f86220ab`). Not this lane's path |
| 12 | `git apply --check research/plan/evidence/r/LAB-R3-IDENTITIES-wiring-e8l-k07.patch` | 0 | clean |

### Manual mutation of the PG route case (outside any runner; l3)

| Edit in `optimization/__init__.py` | The route case |
|---|---|
| `await variants.put_identities(` → `None and variants.put_identities(` | FAILED: the row reads `(None, None)` |
| `if tuple(serving_ref(` → `if False and tuple(serving_ref(` | FAILED: "a refused pair created the variant" (0058's F6 refuses the swapped pair, but only after the variant was published: a partial write the Python check prevents) |
| `base=base, variant=candidate,` → swapped | dies `InvalidRequest` (0058's F6) |

## Mutants (named)

- `tests/l3sql/mutants.py`: **new** `lw9_r3_identities_optional` (`identities is None or variants is None` → `variants is None`); re-cut `lw9_r3_identities_unchecked` (`if tuple(serving_ref(`), `_portless` (→ `identities is None`), `_unwritten` (`await variants.put_identities(`); `_swapped` unchanged. Case: `test_r3_store__writes_both_identities_as_the_variant_is_created`.
- `tests/g/mutants.py` (case `test_lab_optimizations__r3_stores_both_identities_through_d7_and_0058_on_the_pool`): `lab_optimizations_other_function`, `_data_off_the_pool`, `_variants_off_the_pool`, `_portless`, `_identities_bound`.
- `apps/lab/tests/r/run-mutants.mjs`: **R4-X113 flipped back** (an answer omitting `base` is read: `base: opt(nul(IDENTITY))`), killed by the inverted **R4-H06**; new R4-X123 (omitted `variant` read), R4-X124/X125 (a legacy null base/variant refused), R4-X126 (legacy text drops the serving ref); R4-X121 re-cut (legacy shows the bare ref), killed by the re-cut R4-V12.

## Wiring requests

- **WR-R3I-1 (tests/integration/lab_rollout/scenarios_parity.py, E8L k07; needed once this merges).**
  `store` now requires `identities`/`variants`, so k07's two `r3.store(...)` calls raise
  `TypeError` when the e8l stack runs. Exact patch:
  `research/plan/evidence/r/LAB-R3-IDENTITIES-wiring-e8l-k07.patch` (imports `PgLabVariants`;
  passes `identities=(base, nvfp4), variants=PgLabVariants(connector(lab.dsn))` to both calls).
  `git apply --check` clean; NOT RUN (e8l is not this lane's key; without the stack k07 skips
  BLOCKED[stack], so `make api-test` is unaffected). Rerun:
  `INFRX_D_TASK=... uv run --frozen pytest -q tests/integration/lab_rollout -k k07` on the e8l stack.
  E8L's `mutants.py` has no anchor on these lines.
- **WR-R3I-2 (coordinator, r2, optional).** `INFRX_D_TASK=r2 uv run --frozen pytest -q
  tests/r/optimization/test_optimization_pg.py` — run here on l3 instead (passed, row 2).

## Ruling proposals (unnumbered; rulings run through R263)

1. "Every optimization variant R3 creates carries both revision identities: `optimization.store`
   requires `identities=(base, variant)` and the variants port and refuses either missing before
   anything is written; the launched composition is `pilot.lab_optimizations(connect)` (D7 +
   0058's `PgLabVariants` on the Lab pool), the caller naming the pair it registered the variant
   from (WR-LW9-4; with R263)."
2. "The Lab reads a variant's `base`/`variant` as required keys whose value may be null: an answer
   omitting them is not 0058's listing and reads unavailable; null is a variant stored before R3
   recorded identities (before WR-LW9-4, or before 0058 was applied) and the page shows
   `identity not recorded (<serving ref>)` for that side. Supersedes R252 (b)'s optional read
   (WR-LW9-1)."

## Open issues

- 0058 (and 0055) are LOCAL-ONLY: with `LAB_RELEASES_API_URL` set against a database without
  0058, `/lab/v1/optimizations` itself fails, so enabling the Lab's release API still needs 0058
  hosted (R263's condition); the switch stays OFF.
- No production R3 CLI/worker exists: `pilot.lab_optimizations` is the composition such a caller
  uses; wiring a CLI or worker entry that calls it is not in this lane.
- The base identity is not checked against a source policy revision's serving ref (the brief's
  "source policy" wording): nothing in R3 or 0058 names a source policy for a variant. Propose only
  if a later lane links variants to policies.
- `make api-test`'s one failure (row 11) is the base's `tests/i/lab_control` coverage gap (a
  lab-rollout-7 case with no mutant); the coordinator's tip may already carry the fix (merge #66).
- The full `tests/g` mutant list (491) was not rerun; the new and neighbouring anchors were, and
  every pilot/R3 anchor in every list was counted (row 6).

## Estimate (remaining)

optimistic 0.25 h / likely 0.75 h / pessimistic 2 h, confidence medium. Basis: code, tests and
mutants done with 0 survivors; E4 2842/0 on p3. Remaining: coordinator review, WR-R3I-1 applied
and rerun on e8l (~10 min), one verify round. Analogue: lab-sql-lw9 at 0.5/1.25/3.

## Open item (recorded at merge)

- **WR-R3I-OPEN.** `pilot.lab_optimizations` has no caller yet; any later R3 entry (CLI, worker,
  route) must compose through it. "Every variant carries both identities" holds by construction
  because `store()` refuses without them.

## Coordinator rulings

- **R266** — Every variant R3 creates carries both revision identities: `optimization.store`
  requires `identities=(base, variant)` and a variants port and refuses (`invalid_request`,
  nothing written) without them or when the pair's serving refs are not the variant's; production
  composes it through `pilot.lab_optimizations` on the Lab pool (WR-LW9-4; with R263). Proposal 1.
- **R267** — The Lab reads a variant's `base` and `variant` as required keys that may be null: null
  is a legacy row rendered "identity not recorded (<serving ref>)"; an answer that omits either
  key is unavailable (WR-LW9-1; supersedes R252 (b)). Proposal 2.

Numbered in 08 §10 directly after R265 at the lab-r3-identities merge on `codex/w5-merge-68`;
next free R268.

## Merge

- Lane head `aba93c87` merged `--no-ff` onto `004f521d` (merge #68, `codex/w5-merge-68`); no
  conflicts (pilot.py's only change is `lab_optimizations`, ReleaseRecords untouched).
- **WR-R3I-1 applied** (`LAB-R3-IDENTITIES-wiring-e8l-k07.patch`: both `r3.store` calls in
  `tests/integration/lab_rollout/scenarios_parity.py` pass `identities=(base, nvfp4)` and
  `variants=PgLabVariants(connector(lab.dsn))`). k07 rerun for real on the e8l key (free: no
  `infrx-e8l` container before): `runner.py --out <dir> --keep --only k07` → **k07 PASS** (gate
  NOT RUN / exit 3 only because `--only` leaves k01–k06, k08–k10 unrun). K07's five stack mutants
  on the kept stack (`INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e8l pytest
  tests/integration/lab_rollout/test_mutants.py -k 'st_claim_unmeasured or st_version_unprobed or
  st_tokenizer_ignored or st_any_load_source or st_hardware_unchecked or well_formed or
  every_case'`; the literal `-k 'k07 or K07'` selects nothing since the ids are mutant names) →
  **7 passed** (5 killed + 2 list checks). The pristine baseline runs every stack case, so the
  clone needs `apps/lab/node_modules` for k10's UI half (a first attempt without it was
  broken_runner on the baseline, not a survivor). Teardown `--reuse --only none`: no e8l
  container left.
- **R3I-RV-2 applied:** `infrx/state/lab_variants.py`'s docstring names R267 (required keys that
  may be null; supersedes R252 (b)).
- **R3I-RV-3 applied:** `tests/l3sql/test_lw9_routes_pg.py`'s route case also refuses
  `(r3w.BASE, r3w.ident(quantization="fp8"))` (a wrong variant-side identity); rerun on l3.
- WR-R3I-OPEN recorded above and in the update JSON.
- The `tests/i/lab_control` `every_case` failure the lane saw on its base (row 11) is fixed on the
  tip since `271aff13`.
