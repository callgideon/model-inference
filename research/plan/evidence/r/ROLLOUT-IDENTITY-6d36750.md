# ROLLOUT-IDENTITY (LW6): WR-E8L-8 = E8L-F2. R2 recognises a promoted candidate by serving identity

Task: E8L (gate). R2 in tasks.json stays implemented. Lane `rollout-identity`, branch
`codex/w5-rollout-identity`, worktree `.claude/worktrees/codex-w5-rollout-identity`, base
`cd4f6e5b`, code head `6d367503`. Tasklocal keys: `r2` (PG 57534) for the tests/r PG cases,
`e8l` (compose 57400-57499, PG 57432) for the E8L rerun. Everything ran locally. Nothing touched
hosted Supabase, the pilot box, AWS/SSM/S3, Vercel or secrets. No migration was written.
No composition root, `infrx/state/lab_*`, `infrx/lab/control` or `apps/lab` file changed.

## Changed paths
- `apps/infrx-api/infrx/rollouts/control/__init__.py`: adds `serving_identity(ref)`, which is
  the R188 ref with its `deployment_revision_id` removed: `lab:serving:<provider>@sha256:<digest>`.
  The digest is the JCS digest of the ServingRevision, and that record carries
  `serving_version_id` and `provider_org_id`. `Controller._converge` now tests candidate
  membership with this identity. It also stops when the alias is exactly on `policy.baseline_ref`.
  The module docstring R2.c records both rules. The ref format is unchanged, so R188/R208 still hold.
- `apps/infrx-api/tests/r/control/test_control.py`: adds 2 cases (see below).
- `apps/infrx-api/tests/r/control/test_control_pg.py`: adds 1 case, the r2 PG twin.
- `apps/infrx-api/tests/r/control/mutants.py`: adds 5 named mutants, re-anchors `r2_converge_any_alias`
  and adds the PROMO/DEPLOY case names.

Path note: the brief names `tests/r/rollouts/**`, which does not exist. R2's suite is
`tests/r/control/` (its own `mutants.py`/`test_mutants.py`, already in the api-mutants list),
so the new cases live there and need no Makefile wiring.

## Why the baseline guard is included
In the D9 fixtures the baseline and candidate refs share one digest (`'e'*64`), which is a
deployment-only rollout. For such a policy the candidate's identity equals the baseline's.
Without the guard, an identity-only test re-lists the baseline on every restart pass: the
existing PG restart case `test_r2_pg_a_restart_converges_...` then fails with 2 rollbacks.
The rule `current == baseline_ref or identity(current) not in candidates` is the smallest one
that meets three conditions:
- a promoted candidate converges;
- a deployment-only candidate still rolls back;
- the baseline itself is never re-listed.

## Fail-first (recorded before the fix, commit 256792f9)
- `test_r2_a_promoted_candidate_is_recognised_by_its_serving_identity` was RED on the old
  code. The alias stayed on `...0000002a...@sha256:bbbb` (the promoted candidate) when it
  should have moved to the baseline `...00000028...@sha256:ffff`.
- `test_r2_pg_a_promoted_candidate_is_rolled_back_by_its_serving_identity` (INFRX_D_TASK=r2)
  was RED the same way (`...0000000009@sha256:e` vs the baseline `...0000000001@sha256:e`).
- `test_r2_a_deployment_only_candidate_rolls_back_once_and_never_relists_the_baseline` is a
  guard. It is green on the old code and is killed by `r2_converge_relists_baseline` on the new code.

## Named mutants (tests/r/control/mutants.py), all killed
| mutant | invariant | case |
|---|---|---|
| r2_converge_full_ref_membership | the E8L-F2 defect itself: the old full-ref set and `current not in candidates` put back | PROMO |
| r2_identity_is_the_full_ref | the identity drops the deployment revision (`return ref`) | PROMO |
| r2_identity_drops_provider | the identity keeps the provider | PROMO |
| r2_identity_drops_digest | the identity keeps the serving revision's digest | PROMO, RACE |
| r2_converge_relists_baseline | an alias exactly on the baseline is never re-listed | DEPLOY |
| r2_converge_any_alias (re-anchored) | an alias moved on by someone else is left alone | RACE |

## Commands
| command | exit | result |
|---|---|---|
| `uv run --frozen pytest -q tests/r/control/test_control.py` (before the fix) | 1 | 1 failed, 24 passed |
| `INFRX_D_TASK=r2 uv run --frozen pytest -q tests/r/control/test_control_pg.py` (before the fix) | 1 | 1 failed, 3 passed |
| `uv run --frozen pytest -q tests/r/control/test_control.py` | 0 | 25 passed |
| `INFRX_D_TASK=r2 uv run --frozen pytest -q tests/r/control/test_control_pg.py` | 0 | 4 passed |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/r/control/test_mutants.py` | 0 | 70 passed (68 mutants killed, 0 survivors; well_formed + every_case green) |
| `INFRX_D_TASK=r2 make api-test` | 2 | 6609 passed, 7 failed, 161 skipped, 9 xfailed (1:26:08) |

All 7 api-test failures are `tests/d/test_outbox_relay.py::*[valkey]`, with
`pgharness.ForeignContainer: infrx-d2-valkey exists and is not this checkout's`. They come from the
foreign leftovers of finished agents: infrx-d1-postgres, infrx-d2-postgres/valkey,
infrx-t2f-postgres/s3/clickhouse, infrx-b3-postgres and 3 `infrx-e5l_*` volumes. Per the brief,
I did not touch them. None of the 7 is in this lane's paths. The i8-lock cases, if held, are
among the 161 skips (NOT RUN).

## E8L k06 rerun (the gate cell)
Setup: a shared clone of this head (`6d367503`) with `codex/w5-lab-rollout-2` (`f6c074f6`,
batch #33, not yet on the tip) merged cleanly (merge commit `2706a321`), on the `e8l` stack.
I waited for the lab-rollout-2 worktree's own `INFRX_D_TASK=e8l` api-test to exit before starting.
Raw evidence: `research/plan/evidence/e/E8L-raw-rollout-identity-6d36750/`.

| run | command | k06 | note |
|---|---|---|---|
| unpatched | `runner.py --keep --only k06` | **FAIL** (exit 1) | E8L-F2 is gone. R2 now recognises the promoted alias and calls L3's rollback. That rollback raises `NotFound: no earlier listing of this alias serves that ref`: this is **E8L-F4** below. |
| world patch (WR-E8L-9) | `--reuse --keep --only k06` | **PASS** (exit 3: gate NOT RUN, other cells unselected) | |
| negative control: world patch plus R2 reverted to base | `--reuse --keep --only k06` | **FAIL** (exit 1) | The old E8L-F2 text: "L3 reads it as `...00000de8...@sha256:39ddd03c...`, the policy R1 routes names `...0000005e...@sha256:39ddd03c...`". |
| full, world patch | `--reuse` (then teardown) | k01-k07 **PASS**, k08 NOT RUN[P-08], k09 NOT RUN[WR-R2-3], k10 NOT RUN[lab-ui-swap] | cells: ROLLOUT-PIN NOT RUN, ROLLOUT-RECOVER NOT RUN (k09), OPT-PARITY NOT RUN; gate **NOT RUN** (exit 3; it was FAIL before) |

The negative control shows that k06 needs **both** changes: the R2 fix (this lane) and the world fix
(WR-E8L-9). Teardown happened: no `infrx-e8l-*` container is left after the full run.

### E8L-F4 (found by the rerun; a world defect outside this lane's paths)
The k06 policy's `baseline_ref` is the world's opaque stand-in,
`q8.serving_ref(cc.SERVING) = lab:serving:<NEMO>:<serving_version_id>@sha256:eeee...`. L3's real
`operations.Serving.rollback` finds its target by recomputing each listing's R188 ref and
comparing it with `to_serving_ref`. The stand-in never matches a real listing, so every correct
rollback reaches `NotFound`. E8L-F2 hid this, because the old `_converge` never called rollback.
In production the baseline is L3's own ref of the listed deployment (R191/R208), so the product
is unaffected. This is a fixture gap only.

## Wiring requests
- **WR-E8L-9** (lab-rollout owner, `tests/integration/lab_rollout/scenarios_recover.py`; apply after
  #33 merges). This is the exact patch `E8L-raw-rollout-identity-6d36750/WR-E8L-9-k06-world.patch`.
  k06 launches its own policy with `baseline_ref = infrx.lab_serving_ref(<the alias's listed deployment>)`
  and builds its runs on that ref. It changes k06 only. The shared `lab.BASE` stays as it is,
  because LSQ5/WR-E8L-2b showed that swapping it regresses k02/k03/k05. Proven by the patched runs above.
- **WR-E8L-10** (lab-rollout owner, `tests/integration/lab_rollout/mutants.py` +
  `scenarios_recover.py` docstring): remove k06 from `KNOWN_FAIL`. Add a stack mutant naming k06:
  `infrx/rollouts/control/__init__.py`, `"    return f\"{head.rpartition(':')[0]}@{digest}\""` ->
  `"    return ref"`. The negative control above is its kill. Rewrite k06's docstring from
  "E8L-F2 KNOWN_FAIL" to "fixed by ROLLOUT-IDENTITY".
- No Makefile, lockfile or composition-root change is needed. `tests/r/control/test_mutants.py`
  is already in api-mutants.

## Proposed ruling (unnumbered; R213 is the latest, so R214 would be next)
"R2's candidate-set membership (`Controller._converge`) compares by serving identity: the R188 ref
without its `deployment_revision_id`, i.e. the provider and the JCS digest of the serving revision,
which carries `serving_version_id`. So L3's promotion of a candidate's serving revision under a fresh
deployment revision is still that policy's candidate. An alias exactly on `baseline_ref` is never
re-listed, so a deployment-only candidate (same serving revision as the baseline) rolls back once and
never flaps. The ref format is unchanged (R188/R191/R208)."

## Not changed (a deliberate scope choice)
`_unbound`'s report binding (`report_not_this_baseline` / `report_not_this_release`) keeps the
exact full ref. Evidence binds to what was evaluated. Under identity, a deployment-only rollout's
report could bind a "candidate" run made on the baseline deployment itself. If the coordinator
rules otherwise, the change is two lines plus two mutants.

## Estimate (remaining for E8L after this lane)
Merge this lane with #33, then apply WR-E8L-9/10 and run one E8L rerun: 1 / 2 / 4 h, confidence
medium. Basis: session-03 merge-lane analogues and this lane's measured rerun (57.1 s for the full
runner on a reused stack, from its verdict.json). The E8L gate stays NOT RUN until k08 (P-08 GPU),
k09 (WR-R2-3) and k10 (the lab-ui-swap e2e suite) land. Those are outside this lane.

## Fix round (0-RI-1), code head 7a5161ad (commits 39de8f0c, 59576dad, 7a5161ad)
**Finding 0-RI-1 (major):** identity membership let a pass over a rolled-back release revert a
later, deliberate listing of the same serving version (a re-promotion under a fresh deployment
revision; for a deployment-only rollout, any later redeploy of the baseline's serving version).
The docstring claimed "left alone", and the proposed ruling did not mention the case.

**Chosen scope:** identity membership belongs to the rollback *decision*. A pass over the stored
`rolled_back` row matches the policy's named candidate refs only. The decision path covers a
rollback verdict in `step`, the lost race that rereads one, and `emergency_rollback`, including
an operator's repeat on an already rolled-back row. The fence-bound option ("converge by identity
up to the alias fence seen at the decision") was not taken: it needs the alias fence stored on
D9's row, which is outside this lane (`infrx/state/lab_*.py`, 0039). The stopped-pass-loop option
was not taken either: the loop cannot know "converged" across a restart without storage.
Consequence, stated in the docstring: a controller killed between D9 and L3 on a *promoted*
listing is not finished by the pass. The operator's stop finishes it (pinned). On a named ref
the restart still converges (the k04 path is unchanged).

- `infrx/rollouts/control/__init__.py`: `_converge(..., decided=False)` picks the key
  (`serving_identity` or the full ref). The two decision call sites pass `decided=True`: the
  rollback verdict in `step`, and `emergency_rollback`. The restart line in `step` keeps its
  original text, so I7's `i7_restart_does_not_converge` anchor
  (`tests/i/lab_rollout/mutants.py`) still holds. At 59576dad the flag ran the other way, that
  anchor was lost, and `api-test` caught it (below).
  The module docstring is rewritten to name the behaviour.
- Fail-first `39de8f0c` (red):
  `test_r2_a_later_listing_of_the_same_serving_version_is_left_alone_after_a_rollback` covers
  re-promotion, the deployment-only redeploy, and a crash on a promoted listing finished by the
  operator's stop. Its r2 PG twin is `test_r2_pg_a_later_listing_of_the_same_serving_version_is_left_alone`.
  Both failed at `ca6e635e` (alias moved back, 2 rollbacks) and pass at `59576dad`.
- The deployment-only case now also repeats the operator's stop. Without that repeat, the
  baseline guard (`r2_converge_relists_baseline`) survived, because the pass path no longer
  reaches identity.
- Mutants: three are new. `r2_restart_converges_by_identity` adds `decided=True` to the restart
  pass (kills LATER). `r2_breach_converges_by_full_ref` drops `decided=True` from the verdict path
  (kills PROMO). `r2_emergency_converges_by_full_ref` drops it from the operator's stop (kills
  PROMO and LATER). `r2_converge_full_ref_membership` is now
  `key = serving_identity if decided else str` -> `key = str`: the full-ref comparison put back
  (kills PROMO).
  `r2_no_converge_on_restart`, `r2_converge_any_alias` and `r2_converge_relists_baseline` were
  re-anchored on the new text.

| command | exit | result |
|---|---|---|
| `pytest -q tests/r/control/test_control.py` (fail-first, 39de8f0c) | 1 | 1 failed, 25 passed |
| `INFRX_D_TASK=r2 pytest -q tests/r/control/test_control_pg.py` (fail-first) | 1 | 1 failed, 4 passed |
| `INFRX_D_TASK=r2 make api-test` at 59576dad | 2 | 6618 passed, 8 failed, 153 skipped, 9 xfailed (82 min). 7 of the failures are `tests/d/test_outbox_relay [valkey]` (the foreign `infrx-d2-valkey` leftover, the same as at 6d36750, not touched). 1 is `tests/i/lab_rollout::i7_restart_does_not_converge`, misdeclared because its anchor was lost; fixed at 7a5161ad |
| `pytest -q tests/r/control/test_control.py` at 7a5161ad | 0 | 26 passed |
| `INFRX_D_TASK=r2 pytest -q tests/r/control/test_control_pg.py` at 7a5161ad | 0 | 5 passed |
| `INFRX_MUTANTS=all pytest -q tests/r/control/test_mutants.py` at 7a5161ad | 0 | 73 passed: 71 mutants killed, 0 survivors |
| `INFRX_MUTANTS=all pytest -q tests/i/lab_rollout/test_mutants.py` + `tests/i/lab_rollout` at 7a5161ad | 0 | 17 passed; 9 passed |
| E8L full runner (fresh stack, torn down) on a clone of 7a5161ad + `codex/w5-lab-rollout-2` f6c074f6 + the WR-E8L-9 patch | 3 | k01-k07 PASS (k06 PASS); k08/k09/k10 NOT RUN; gate NOT RUN. `E8L-raw-rollout-identity-7a5161a/full/verdict.json`. The same result at 59576dad (75.8 s) |

The full `api-test` was not repeated at 7a5161ad (82 min). The delta from 59576dad inverts the
flag's default only, and every api-test suite that names `infrx/rollouts/control` was rerun green above. (The E8L stack mutant list was not run.)

**Proposed ruling, revised (unnumbered; this supersedes the text above):** "R2's candidate-set
membership compares by serving identity (the R188 ref without its `deployment_revision_id`), but
only when the rollback is decided: a rollback verdict, the lost race that rereads one, or the
operator's `emergency_rollback`. So L3's promotion of a candidate under a fresh deployment
revision is rolled back. A later pass over a `rolled_back` release matches the policy's named
candidate refs only, and leaves alone any later listing of the same serving version under
another deployment revision (a deliberate re-promotion or redeploy). An interrupted rollback of a
promoted listing is finished by the operator's stop. An alias exactly on `baseline_ref` is never
re-listed. The ref format is unchanged (R188/R191/R208)."

Rulings: the revised proposal is numbered R216 in `08-contracts-v1-encoding.md` §10 at the rollout-identity merge on `codex/w5-merge-35` (2026-09-29); next free R217.
