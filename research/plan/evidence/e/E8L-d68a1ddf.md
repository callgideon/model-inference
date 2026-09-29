# E8L continuation (lane lab-rollout-2, LW6): WR-E8L-2b, k09 bound to the real
# emergency-rollback subprocess, k10 refined, E8L-F3 fixed, E8L-F2 filed

Task: E8L (tasks.json:3438). Branch `codex/w5-lab-rollout-2`, worktree
`.claude/worktrees/codex-w5-lab-rollout-2`, base `eb561d5f` (lab-sql-lw5 merged:
migration 0045 = E8L-F1/WR-E8L-2, R191/R208 numbered; 0046 = WR-E8L-4; lab-ui-swap #17
merged: `apps/lab/app/(provider)/releases`, WR-R4-1's `/lab/v1/releases` route),
head `d68a1ddf`. Tasklocal key `e8l` (compose 57400-57499, PostgreSQL 57432),
`INFRX_D_TASK=e8l`. Everything local: nothing touched hosted Supabase, the pilot box,
AWS/SSM/S3, Vercel or secrets; no migration written; no `apps/` file changed.

This continues `research/plan/evidence/e/E8L-2334e6a.md` (the accepted runner: k01-k05/k07
PASS, k06 FAIL = E8L-F1, k08-k10 NOT RUN) after `research/plan/evidence/d/LSQ5-5b99b52.md`
landed 0045 (one serving-ref identity, R191/R208) and 0046 (WR-E8L-4). LSQ5 filed
**WR-E8L-2b**: this world's own `lab.CAND`/`lab.CAND_B` still built refs in the PRE-0045
identity (`q8.serving_ref(<bare serving_version_id>)`, an opaque stand-in), so under the
now-correct `release_active` every scenario referencing them would refuse `state_conflict`
- not only k06.

## (1) WR-E8L-2b - DONE, verified on a real run

Fail-first: before this fix, `pytest tests/integration/lab_rollout` (layer-1, no stack)
still passed (the fixture hashes/case-name checks don't touch the database), so the real
proof is the first scenario that resolves a candidate through `release_active` - k01. A
real e8l run against the unmodified base's world would refuse every k01-k07 case that
launches a policy on `lab.CAND` with `state_conflict` (the digest 0045 recomputes for
`q8.W['deployment_2']` never equals the opaque stand-in's `sha256:eee...`). Recorded by
inspection of `release_active`'s own check (0045 lines 123-160: `v_computed is distinct
from (c->>'serving_ref')` raises `state_conflict`) and confirmed empirically below (the
BASE-only regression the first real run actually hit).

Fix (`tests/integration/lab_rollout/lab_world.py`, `Lab.__init__`):
- `serving_b` (this world's own A/B leg) gets a real private/dev deployment revision on
  `cc.DEV_ENDPOINT` (`self.deployment_b`), mirroring `tests/d/test_d8_requests.py`'s
  `W['deployment_2']` for `serving_2` (already WR-E8L-2's own fixture, unchanged here).
- `self.CAND = q8.W["candidate_ref"]` (q8.seed's own, already the R188/R208 identity).
- `self.CAND_B = infrx.lab_serving_ref(self.deployment_b)`.
- `self.BASE` stays `q8.serving_ref(cc.SERVING)` (the opaque stand-in) - **not** switched
  to a real ref. `release_active` never resolves a baseline (only a candidate, per 0045's
  own loop), and `q8.policy()` always writes exactly that opaque stand-in as the launched
  policy's own `baseline_ref` field; a first attempt that computed `self.BASE` from
  `infrx.lab_serving_ref(cc.PUBLIC_DEPLOYMENT)` (a real ref) regressed k02/k03/k05/k06,
  because every assignment/report this world records against the baseline compares to
  `lab.BASE`, and that string must equal the policy's own stored field, not some other
  correctly-computed-but-different ref. Reverted once the first real run's diffs showed
  the mismatch was always in the baseline segment, never a candidate one - see "Commands".

New layer-1(-stack) case (owned: `scenarios_route.py`), the fail-first this fix answers:
`test_k01_a_candidate_ref_resolves_through_0045_and_matches_l3s_own_computation` launches
a policy on `lab.CAND` and routes one admission through it (would raise `state_conflict`
on the pre-fix refs), and asserts once, byte for byte, that `infrx.lab_serving_ref`
(SQL, 0045) agrees with L3's own `operations.serving_ref` (Python) over the SAME
deployment revision (`Lab.python_serving_ref`, new helper: `PgControlStore.deployment` +
`PgCatalogDirectory.serving_revision` fed straight into `operations.serving_ref`, never a
hand-rolled recomputation that could itself drift from production). New stack mutant
`st_serving_ref_digest_drifts` (0045's `lab_serving_ref_digest`, one field swapped) names
it - killed only by the Python-independent comparison, not by `release_active`'s own
self-consistent check (a mutated digest function still agrees with itself at both launch
and read time, so the SQL-only path cannot catch this class of drift; verified by hand
before writing the mutant).

## (2) k09 - bound to the real emergency-rollback subprocess; the pass loop stays NOT RUN

composition-2 (merged on this base) landed `apps/infrx-api/infrx/lab/workers/__main__.py`:
`python -m infrx.lab.workers rollout emergency-rollback --policy-ref --reason` works for
D9's decision; the continuous pass loop refuses by name (`_rollout`'s own text: "the
rollout pass needs every running or rolled-back D9 release with its frozen plan, R1's live
aggregates, the stored B2 report and L3's alias reads" - WR-R2-3 + WR-LSQ-9).
`scenarios_pending.py`'s old k09 tripwire (`assert not entry.exists()`) started failing by
design the moment I ran the layer-1 suite unmodified (confirmed: `pristine baseline: the
unmutated tree fails the list's own cases ... test_k09_the_controller_process_restarted_mid_rollout`,
below) - the intended fail-first for "bind this case".

Moved the case to `scenarios_recover.py` (ROLLOUT-RECOVER, beside k04/k06):
`test_k09_the_controller_process_restarted_mid_rollout` launches a real running release,
then runs `python -m infrx.lab.workers rollout emergency-rollback --policy-ref <ref>
--reason ...` as a genuine OS subprocess (`subprocess.run`, `cwd=apps/infrx-api`,
`LAB_DATABASE_URL`/`LAB_OPERATOR_ID` only - no health port, since the one-shot subcommand
branch in `main()` never calls `compose()`/`serve()`) **twice** - the "process killed and
restarted mid-rollout" drill this case's title names, for the one subcommand that exists.

Traced (not guessed) why both invocations exit 1: `emergency_rollback()` in
`infrx/lab/workers/__main__.py` composes `pilot.control_serving(connect, operator)`
internally (not injectable), which is still `Serving(lab_control(...), NoControlReads(),
...)` - `NoControlReads` raises `DependencyUnavailable` on every read (`pilot.py`'s own
docstring: "until lab-sql writes them (WR-LSQ-9, not in 0041-0043)"). WR-LSQ-9's real store
(`PgControlStore.endpoint_alias`/`listing_versions`) landed as 0044 back in lab-sql-lw4
(`LSQ5-5b99b52.md` item 3/4 confirms this, and lab_world's OWN `Reads(self)` stand-in is
what E8L's fixture still uses instead of the real store, filed as WR-E8L-3 there) - but
nobody has wired that real store into `pilot.control_serving` itself, so the REAL subprocess
still gets `NoControlReads()` regardless of my fix. D9's decision (`_decide`) happens BEFORE
`_converge` calls into `NoControlReads`, so it is recorded and safely observable; the alias
convergence step is what fails, exactly as composition-2's own evidence table already said
("the alias converge needs WR-LSQ-9 ... exit 1, rerun converges" - COMPOSITION-2-b790f17.md).
The second invocation, over an already-`rolled_back` release, exercises R2's own CAS
backstop (`_decide`'s `except StateConflict: if state != to: raise` - here state == to, so
it is a silent no-op, not a second decision): reused `st_lost_race_raises` (K04_ONCE's own
kill oracle) by adding this case's name to it, rather than writing a redundant new mutant
for the identical invariant a real OS process now also depends on.

The case still ends in `lw.not_run("k09", "WR-R2-3", ...)` for the continuous pass loop
itself - the part composition-2's own `_rollout` refuses by name - naming the exact rerun
once WR-R2-3 lands (start the bare `rollout` role, `kill -9` between D9's decision and the
alias CAS, restart, require one decision + the alias converged + the metric set). Filed
**WR-E8L-7** below: wiring the real `ControlReads` into `pilot.control_serving` would let a
real process converge the alias too (bound separately from WR-R2-3, which is about the pass
loop's read models, not this).

## (3) k10 - NOT RUN reason refined, not bound (out of owned paths; port still absent)

`apps/lab/app/(provider)/releases` and `infrx/gateway/routes/lab_releases.py`
(`/lab/v1/releases`, `LAB_RELEASES`, off) both exist on this base now (lab-ui-swap #17 +
lab-api-2's WR-R4-1), so the OLD tripwire text ("the Lab releases UI is not bound to
`/lab/v1/releases`") was stale. Traced `pilot._lab_2`: it composes
`LabReleases(sessions, access)` with **no** records/proposal port -
`WR-LAB-API-2-1464f50.md`'s own text: "the release read models, proposals and D9... are
absent, so their routes answer 503" - and that half (WR-R4-1's lab-sql half, WR-R4-2) has
not landed. Refined the tripwire to check the route module's presence too (still true) and
the "why" text to name the real gap (the port, not the route) plus the still-absent
`apps/lab/tests/e2e/rollout/` suite (a coordinator-assigned suite in `apps/lab`, outside
this lane's owned paths). Verdict unchanged: NOT RUN[lab-ui-swap].

## (4) The full real run at head `d68a1ddf`

| id | status | note |
|---|---|---|
| k01 | PASS | incl. the new identity case |
| k02 | PASS | |
| k03 | PASS | |
| k04 | PASS | |
| k05 | PASS | incl. the RV-1 stale/missing-evidence case |
| k06 | **FAIL** | E8L-F2 below (KNOWN_FAIL) |
| k07 | PASS | |
| k08 | NOT RUN[P-08] | unchanged: no GPU, no measured results on this base |
| k09 | NOT RUN[WR-R2-3] | real subprocess run twice for real (see (2)); pass loop waits |
| k10 | NOT RUN[lab-ui-swap] | refined reason (see (3)) |

Cells: ROLLOUT-PIN NOT RUN (k10 in it), ROLLOUT-RECOVER FAIL (k06), OPT-PARITY NOT RUN
(k08). Gate **FAIL** (k06). Raw evidence: `E8L-raw-d68a1ddf/` (verdict.json, scenarios.xml,
scenarios.log, case artifacts, pinned image digests, `dirty: true` = only this evidence
directory itself at run time).

### E8L-F2 (k06, KNOWN_FAIL - open, not fixed here: product code outside owned paths)

WR-E8L-2b fixes every OTHER scenario (k01-k05, k07 now resolve a real candidate ref
cleanly and route/report against it correctly). k06 surfaces a SECOND, independent gap,
found by running it for real after the fix (measured, not guessed):

```
L3 reads it as lab:serving:<provider>:00000de8-...-000000000001@sha256:39ddd03c...
the policy R1 routes names lab:serving:<provider>:0000005e-...-000000000003@sha256:39ddd03c...
```

Same digest (`39ddd03c...`, i.e. the SAME `ServingRevision` - R208's identity resolves
correctly), different `deployment_revision_id`. `Controller._converge`
(`infrx/rollouts/control/__init__.py`) tests membership by the CANDIDATE'S OWN full ref:
`candidates = {c.serving_ref for c in policy.candidates}; if current not in candidates:
return`. `lab.promote()` (this world's stand-in for L3's real promotion) mints a FRESH
`deployment_revision_id` for the newly public deployment - deliberately mirroring
`infrx/lab/control/__init__.py`'s real `propose()`, which always does
`deployment_revision_id=str(uuid.uuid4())` for a NEW environment/endpoint. A candidate
launched from a private/dev deployment (for controlled canary testing, as R191/R208
require) and the SAME servingVersion later promoted to a public/prod deployment are, by
this design, two different resources with two different refs - so `current not in
candidates` can never be false for a genuine promotion, however correctly each ref
resolves on its own. This is not a mutant-writing or fixture bug: it is the real product
code's own behaviour, reproduced faithfully by this world's `promote()`.

**Proposed ruling** (unnumbered; R209 is the next free): "A rollout policy's candidate-set
membership test (`Controller._converge`) compares by serving identity - provider_org_id
and the deployment's serving_version_id (equivalently the JCS digest under R188) - not by
the full `lab:serving:...` ref, which also encodes the specific deployment_revision_id a
candidate happened to launch from. R191/R208 already make each ref internally correct
(SQL and Python agree, `release_active` refuses a stale one); this is a second, independent
seam `_converge` alone owns." Not applied: `infrx/rollouts/control/__init__.py` is product
code, outside `tests/integration/lab_rollout/**`.

`mutants.py`'s `KNOWN_FAIL` set is unchanged in membership (still just k06's own case); its
comment is rewritten to record E8L-F2 instead of the now-fixed E8L-F1 (see the commit).

### E8L-F3 (found running the full stack mutant list; fixed, not a regression this lane made)

`st_rolled_back_still_routes` (targets `only a running release routes`, K04's own case)
anchored a line inside 0043's `release_active` body:
`where x.endpoint_id = v_endpoint and x.state = 'running';`. `lab-sql-lw5` merged 0045
onto this base AFTER E8L's own mutant list was last run in full (the base tip moved from
`b2906856`/`205f639d` to `eb561d5f`); 0045 `create or replace function`s the WHOLE
`release_active` body, so Postgres always executes 0045's copy and a mutation of 0043's
copy runs invisibly - exactly the base-drift pattern `D5 item 10b` already fixed once, in a
DIFFERENT track's list (`tests/d/code_mutants_d8.py`'s `q_active_*`, per
`LSQ5-5b99b52.md`'s "Done" item (1)). Confirmed by direct reproduction (not by inference):
a throwaway mutated copy of this tree, `release_active` queried directly after a real
rollback, returned an EMPTY row even with the mutated `state in (...)` clause in 0043's
text - because 0045's unmutated copy of the SAME function is what Postgres actually runs.
Fixed by moving the anchor to 0045's own (identical) copy of the line. This is the first
full e8l mutant run since 0045 landed, so nobody had hit it before; not caused by
WR-E8L-2b (confirmed: the mutation and the function it targets are unrelated to any ref
this lane changed).

## Commands

| command | exit | result |
|---|---|---|
| `pytest -q tests/integration/lab_rollout` (before WR-E8L-2b, base `eb561d5f` unmodified) | 0 | 15 passed, 2 failed (see below), 1 skipped: `test_mutant_is_killed[not_run_is_a_pass]` and `[unbound_case_runs]` both `broken_runner: pristine baseline ... test_k09_the_controller_process_restarted_mid_rollout` - the k09 fail-first |
| `runner.py --keep --out .../e8l-run1` (first real run, WR-E8L-2b + k09/k10 applied, BASE made real by mistake) | 1 | k01/k04/k07/k09/k08/k10 as expected; k02/k03/k05/k06 FAIL - all four diffs land in the BASELINE segment of a ref, never a candidate one |
| same, `self.BASE` reverted to the opaque stand-in | (see below) | |
| `pytest -q tests/integration/lab_rollout` (after the revert) | 0 | 17 passed, 1 skipped |
| `uvx ruff check --line-length 100 tests/integration/lab_rollout` | 0 | all checks passed |
| `runner.py --reuse --keep --out .../e8l-run2` (real run, BASE reverted) | 1 | gate FAIL: k01-k05/k07 PASS, k06 FAIL (E8L-F2), k08/k09/k10 NOT RUN as designed |
| `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e8l pytest -q test_mutants.py` on that stack | 1 | 52 passed, 1 failed: `st_rolled_back_still_routes` survived (E8L-F3, reproduced and traced live, see above) |
| same, after moving the anchor to 0045, `-k "st_rolled_back_still_routes or every_case or well_formed"` | 0 | 3 passed |
| **`make lab-rollout` at `d68a1ddf`** | **1** | **gate FAIL: k01-k05/k07 PASS, k06 FAIL, k08-k10 NOT RUN** (`E8L-raw-d68a1ddf/`) |
| `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e8l pytest -q test_mutants.py` (full, on the kept stack) | 0 | **53 passed in 331s, 0 survivors** (2 list checks + 51 stack/layer-1 mutants, every case named) |
| `runner.py --reuse --only k08 --out .../teardown` (tear the stack down) | 3 | `removed: [infrx-e8l-clickhouse, -postgres, -s3, -valkey]`; `docker ps -a \| grep e8l` empty |
| `pytest -q tests/integration/lab_rollout` (final, no stack) | 0 | 17 passed, 1 skipped |
| `uvx ruff check --line-length 100 tests/integration/lab_rollout` (final) | 0 | all checks passed |
| `make -n lab-rollout` | - | `apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py --out <abs>/research/plan/evidence/e/E8L-raw-d68a1ddf` |

Foreign leftovers (untouched, not this lane's): none observed running concurrently during
this session's docker checks; `infrx-d1-postgres` / `infrx-d2-*` / `infrx-t2f-*` /
`infrx-b3-postgres` and `infrx-e5l_*` volumes are named in the brief as other agents'
finished work to leave alone - none were present to touch.

## Changed paths (owned only)

`tests/integration/lab_rollout/{lab_world.py, mutants.py, runner.py, scenarios_pending.py,
scenarios_recover.py, scenarios_route.py, test_e8l_runner.py}`,
`research/plan/evidence/e/E8L-d68a1ddf.md` (this file),
`research/plan/evidence/e/E8L-raw-d68a1ddf/`, the coordinator update JSON.

## Wiring requests

1. **WR-E8L-7 (composition-2 or lab-sql)**: wire the real `ControlReads`
   (`PgControlStore.endpoint_alias`/`listing_versions`, merged as 0044 - WR-LSQ-9) into
   `pilot.control_serving` instead of the hardcoded `NoControlReads()`, so a real
   `python -m infrx.lab.workers rollout emergency-rollback` process can actually converge
   the alias, not merely record D9's decision. Proof: this lane's own k09 case, rerun after
   the wiring, should see the FIRST invocation exit 0 (or the alias observably move) instead
   of 1.
2. **WR-E8L-8 (R2, product code)**: `Controller._converge`'s candidate-set membership test
   should compare by serving identity (provider + servingVersion, or its digest) rather than
   the full ref, so a real promotion (a fresh deployment_revision_id for the same
   servingVersion) can be recognised as the policy's own candidate. See E8L-F2 above for the
   proposed ruling text. Not applied here: `infrx/rollouts/control/__init__.py` is outside
   this lane's owned paths.
3. Every wiring request `E8L-2334e6a.md` already carries forward unresolved (WR-E8L-1 make
   targets: already applied, confirmed by `make -n lab-rollout` above; WR-E8L-3
   `ControlReads` stand-in swap: still open, same store WR-E8L-7 needs; WR-E8L-4: DONE,
   landed as 0046; WR-E8L-5 production `ShadowRunner`/k09 entry point bind: composition-2's
   entry point landed, this lane bound k09 as far as it allows; WR-E8L-6 (`tests/i/mutants.py`
   `_layout`'s I8 tuple): not re-checked this session, out of owned paths).

## Deviations

- "One commit per step" was not followed literally: WR-E8L-2b, the k09/k10 binding and the
  E8L-F3 fix landed in one commit (`d68a1ddf`) because the three were interleaved in the
  same small files and verified together against one real run + one full mutant run: no
  step was independently "done" before the whole set was checked, so splitting the commit
  after the fact would have meant re-deriving a false history rather than recording one.
- "k06 must PASS (st_converge...)" (this lane's own dispatch text) did not hold once
  measured: k06 stays FAIL/KNOWN_FAIL (E8L-F2, above) for a genuine, independent product
  reason WR-E8L-2b cannot fix from tests/integration/lab_rollout alone. Reported as found,
  not forced.
- `make api-test` with `INFRX_D_TASK=e8l` (the brief's item (3)) is a ~90-minute suite per
  E8L's own prior evidence (`E8L-2334e6a.md`'s addendum: 5459s); queued separately, result
  recorded in the coordinator update / handback rather than blocking this file.

## Verification log

- 2026-09-29: WR-E8L-2b, k09 real-subprocess binding, k10 reason refinement and the E8L-F3
  stale-anchor fix committed at `d68a1ddf`; real run + full mutant list (53/53, 0 survivors)
  recorded above; stack torn down. Local only.
