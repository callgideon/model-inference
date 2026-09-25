# DOOR-REVOKE (task D10, R123) — 0023: the runtime login loses the two unmarked doors

Lane DOOR-REVOKE, branch `codex/door-revoke`, worktree `.claude/worktrees/codex-door-revoke`.
Base `e607b705`; merged `codex/d10-followup` at its CURRENT head `9d4bd28f` (the brief named
`0d577c4e`; the branch carries two later D10F fix-round commits `a0db994c`, `9d4bd28f` - merged
clean, merge commit `a6b25a4e`). D10-followup's wiring patch NOT applied (union lane's).
Code head `1d0a418d`; this evidence is committed on top of it.
Task-local only: `INFRX_D_TASK=revoke` (PostgreSQL 55459, `infrx-revoke-*`). No hosted DB, no
pilot box, no AWS. No secret printed (test passwords are the lane's local constants or
`secrets.token_urlsafe` in memory).

## Changed paths
| path | change |
|---|---|
| `apps/app/supabase/migrations/0023_runtime_unmarked_door_revoke.sql` | new: `revoke execute on function infrx.admit(jsonb) / infrx.claim_preparation(jsonb) from infrx_runtime` |
| `apps/infrx-api/tests/d/checks_reads.py` | `REVOKED_DOORS`, exact `RUNTIME_FUNCTIONS` (41 names), `check_door_revoke`; `check_reads_privileges` asserts the exact set catalog-wide + service_role keeps the revoked two |
| `apps/infrx-api/tests/d/test_reads.py` | new `test_the_runtime_login_lost_exactly_the_unmarked_doors`; the CREDIT JobStore suite as the runtime login routes only `admit`/`claim_preparation` (its setup) through the owner |
| `apps/infrx-api/tests/d/test_ready.py` | cutover gate: `runtime_unmarked_doors == []` after 0023; a rolled-back re-grant proves the gate still names them |
| `apps/infrx-api/tests/d/test_followup_d10.py` | the preparation is claimed by the owner (the runtime's claim is `claim_preparation_ready`); `fail_preparation` still runs on the runtime login |
| `apps/infrx-api/tests/d/test_upgrade_d10.py` | `D10` split includes `0023_` (else 0023 would run before 0021 creates the role) |
| `apps/infrx-api/tests/d/test_composition_pg.py` | autouse teardown leaves `infrx_runtime` NOLOGIN (order dependence found: composition_pg then test_reads' privilege check failed on `rolcanlogin`) |
| `research/plan/evidence/d/DOOR-REVOKE-runtime-functions.txt` | I8 `privilege_probe.py --allow-functions` list after 0023 (41) |
| `research/plan/evidence/d/DOOR-REVOKE-wiring.patch` | wiring request W-DR1 (layer-3 map + migration pin) |

`infrx/state/pgtesting.py` and `tests/g/test_composition.py`: no change needed (neither pins the
runtime function set; `tests/g/test_composition.py` has only the DSN/set-role check).

## Caller audit (SQL call sites, `apps/infrx-api/infrx/**`, non-test)
Adapters: `PgJobStore._call("admit")` jobstore.py:225 (`admit`), :233 (`admit_credit`);
`PgJobStore._call("claim_preparation")` jobstore.py:288; `PgLifecycle._call("admit_ready")`
lifecycle.py:184; `PgLifecycle._call("claim_preparation_ready")` lifecycle.py:205 (same lines on
all three refs). Who calls the adapters:

| function | base `e607b705` | `codex/w5-merge` `f18c72f7` | `codex/m6-wiring` `28dd1af2` | decision |
|---|---|---|---|---|
| `infrx.admit(jsonb)` | relay.py:167-168 `self.jobs.admit_credit/admit` - ALWAYS | relay.py:192-193 only when `self.readiness is None`; pilot.py:334 `admission_readiness` raises `RuntimeMisconfigured` for a `PgJobStore` without a ReadinessStore, so the PG runtime always takes relay.py:190 `readiness.admit_ready` | relay.py:167-168 ALWAYS (m6 does not touch relay.py) | **revoked** |
| `infrx.claim_preparation(jsonb)` | preparation.py:214 `self.jobs.claim_preparation` - ALWAYS | preparation.py:266 `self.claims.claim_preparation` = `readiness or jobs`; worker/__main__.py:147 always passes `readiness=PgLifecycle(...)` → `claim_preparation_ready` | preparation.py:214 ALWAYS (m6 does not touch preparation.py) | **revoked** |
| `infrx.admit_ready(jsonb)` | - | relay.py:190 | - | kept |
| `infrx.claim_preparation_ready(jsonb)` | - | preparation.py:266 via PgLifecycle | - | kept |
| `infrx.fail_preparation(jsonb)` | jobstore.py:489 `_fenced("fail_preparation")` (merged d10-followup) | preparation.py:320 `getattr(self.jobs, "fail_preparation")` | - | kept |

The w5-merge x m6-wiring merge (`git merge-tree`) conflicts only in `worker/service.py`; its
auto-merged `worker/__main__.py` keeps `readiness=PgLifecycle(connect, limits=limits)` (line 165).
Every other function of the 43 has an adapter call site on w5-merge (grep of `infrx.<f>(` /
`_call("<f>"` over state/operations/gateway/worker/media/scheduling): nothing else is unused, so
nothing else is revoked. Operator/test paths (`tests/**`, `checks_*`, `pgtesting` hooks,
`q3differential`, `m/test_pilot_media`) run as the owner/service_role, which keeps both grants.

**Ordering (verified):** on this branch alone (pre-W5 relay), 0023 breaks the runtime login's
admission: `test_composition_pg::…the_pilot_serves_and_the_worker_connects_on_the_dedicated_login`
answers 503, `psycopg.errors.InsufficientPrivilege: permission denied for function admit`. With
`codex/w5-merge` merged (temporary `git merge --no-commit --no-ff`, then `git merge --abort`) the
same test passes. **0023 must merge and deploy only together with/after W5 (codex/w5-merge).**

## Function set (infrx_runtime EXECUTE, catalog-wide infrx/public, extension-owned excluded)
- before 0023 (0021 + 0022): **43** = 0021's 42 + `infrx.fail_preparation(jsonb)`. Measured: the
  exact-set check against 0001-0022 reports exactly `['infrx.admit(jsonb)',
  'infrx.claim_preparation(jsonb)']` extra, nothing missing.
- after 0023: **41** = `tests/d/checks_reads.py::RUNTIME_FUNCTIONS` (names, asserted by set
  equality) = `DOOR-REVOKE-runtime-functions.txt` = the wiring patch's layer-3 `RUNTIME_FUNCTIONS`
  (all three compared equal by script).
- `service_role` still executes both revoked functions (catalog + an actual call under
  `set local role service_role` that is not refused by name).
- `readiness_cutover_check()->'runtime_unmarked_doors'` = `[]`.

## Commands (all from `apps/infrx-api`, `INFRX_D_TASK=revoke`)
| cmd | head | exit | result |
|---|---|---|---|
| `make api-env` (repo root) | a6b25a4e | 0 | env synced |
| `pytest -q tests/d/test_reads.py -k "lost_exactly or reads_privileges"` before 0023 existed | a6b25a4e+tests | 1 | **2 failed** (fails-before): `infrx_runtime executes ['infrx.admit(jsonb)', 'infrx.claim_preparation(jsonb)'] and not []`; `infrx_runtime still executes infrx.admit(jsonb)` |
| same, with 0023 | b75166d1 | 0 | 2 passed |
| scratch fails-before: migrations dir copied to scratch minus 0023, `sql_for(directory=…)`, both checks | b75166d1 | 0 | `reads_privileges FAILED as expected`, `door_revoke FAILED as expected` |
| `pytest -q tests/d` (first full run; 4 test files were edited after collection) | b75166d1 | 1 | 836 passed, 13 failed, 1 skipped, 5 xfailed: 11 = the unedited runtime-login tests (fixed, below), 1 = composition_pg (W5 ordering, above), 1 = `test_lifecycle_conformance::…transcripts_replay_exactly` (pre-existing: D10-followup evidence §47, needs its W2 wiring) |
| `pytest -q tests/d/test_reads.py tests/d/test_ready.py tests/d/test_followup_d10.py tests/d/test_upgrade_d10.py` | b75166d1 | 0 | 61 passed, 3 xfailed |
| `pytest -q tests/g/test_composition.py` | b75166d1 | 0 | 26 passed |
| with `codex/w5-merge` merged (no commit, aborted after): `pytest -q tests/d/test_composition_pg.py tests/g/test_composition.py tests/d/test_reads.py` | b75166d1+w5 | 1 | 70 passed, 1 failed, 3 xfailed; the 1 = `test_reads_privileges` `rolcanlogin` true left by composition_pg (order dependence, fixed in 1d0a418d); `test_composition_pg.py` alone 3 passed, `test_reads.py` alone 42 passed 3 xfailed |
| `pytest -q tests/d/test_composition_pg.py tests/d/test_reads.py` | 1d0a418d | 1 | 44 passed, 1 failed (composition_pg dedicated login: W5 ordering, expected on this branch), 3 xfailed |
| FINAL `pytest -q tests/d` | 1d0a418d | 1 | **837 passed, 9 failed, 1 skipped, 8 xfailed** (13m52s). 9 = composition_pg dedicated login (W5 ordering, expected here) + lifecycle transcript (inherited, D10F W2) + 7 `test_outbox_relay[valkey]` `HarnessBusy: another run holds /tmp/infrx-d2-valkey-55463.lock` (another lane held the shared default D2 Valkey lock; not a privilege) |
| `pytest -q tests/d/test_outbox_relay.py` (lock free) | 1d0a418d | 0 | 20 passed |
| I8 `privilege_probe.py --role infrx_runtime --allow-functions DOOR-REVOKE-runtime-functions.txt` (fresh 0001-0023 DB on 55459, login given a random in-memory password, NOLOGIN restored) | 1d0a418d | 0 | 63 checks, 0 failed |
| same with the old `D10-runtime-functions.txt` | 1d0a418d | 1 | 65 checks, failed exactly `executes infrx.admit(jsonb)`, `executes infrx.claim_preparation(jsonb)` |

Not run: the layer-3 `tests/integration` RLS matrix (L3-REBASE: 880 cases). It runs in the `e2`
harness namespace (55500+, `infrx-e2-*`), not this lane's port, and its runtime map is in
`tests/integration/pgstate.py` (not owned) - see W-DR1. The `L3-LOGIN-infrx_runtime-functions`
row runs the same `has_function_privilege` catalog query as `check_reads_privileges`, whose
expected set equals the patched `RUNTIME_FUNCTIONS` (41, compared by script).

## Wiring requests
- **W-DR1** (`DOOR-REVOKE-wiring.patch`, `tests/integration/pgstate.py`,
  `tests/integration/test_harness.py`): layer-3 `RUNTIME_FUNCTIONS` loses `admit`/
  `claim_preparation` (41) and the migration pin gains `0023_runtime_unmarked_door_revoke.sql`.
  Written as SEPARATE small hunks that apply AFTER D10-followup's W4 hunks (verified: `git apply
  --check` on scratch copies with W4 applied). The union lane also edits these files: apply W4
  first, then W-DR1.
- **W-DR2** (operator/I8): run `privilege_probe.py --role infrx_runtime --allow-functions
  research/plan/evidence/d/DOOR-REVOKE-runtime-functions.txt` after 0023 (the old
  `D10-runtime-functions.txt` would now FAIL on the two revoked lines). Optional: add
  `("the unmarked admission door", "select infrx.admit('{}'::jsonb)")` to `MUST_DENY`.
- **Ordering (coordinator):** merge `codex/door-revoke` only into a tree that also has
  `codex/w5-merge`; deploy 0023 only after the W5 gateway and worker are the running release.
  A rollback to a pre-W5 runtime needs 0023's rollback grant first.

## Rollback
`grant execute on function infrx.admit(jsonb), infrx.claim_preparation(jsonb) to infrx_runtime;`
Nothing else moved; no job, money or row state depends on the grant. 0023 is re-runnable
(revoking a privilege not held is a no-op).

## Open issues
- `test_lifecycle_conformance::test_the_versioned_acceptance_transcripts_replay_exactly` fails on
  this branch (inherited from d10-followup until its W2 wiring lands) - not 0023.
- `test_composition_pg::…dedicated_login` fails on this branch by design until W5 is merged.

## Estimate (remaining for the coordinator)
optimistic 0.25 h, likely 0.5 h, pessimistic 1.5 h; confidence medium; basis: union merge with
w5-merge + W4 then W-DR1, one composed `tests/d` + layer-3 RLS run.

## Fix round (2026-09-25T22:26Z, on handback head `acc53094`)
No code or SQL changed in owned paths: `0023`, `tests/d/**`, `pgtesting.py` and
`tests/g/test_composition.py` are as handed back. This round adds
`DOOR-REVOKE-wiring-v2.patch` (supersedes `DOOR-REVOKE-wiring.patch`), this section and
`coordinator/updates/DOOR-REVOKE-20260925T2226Z.json`. The new head is the commit that carries them.
Temporary merges were `git merge --no-commit --no-ff codex/wave4b-union` (`9d61d1e1`,
which contains `codex/w5-merge` `f18c72f7` and `codex/d10-followup` `c584f54a`), undone with
`git checkout -- <patched files>` and `git merge --abort`. Nothing was committed from them.

| finding | status | what |
|---|---|---|
| 1-DR-1 (W-DR1 misses mutant e2m75) | **fixed (wiring request, v2)** | `DOOR-REVOKE-wiring-v2.patch` = v1's `pgstate.py`/`test_harness.py` hunks (byte-identical) **+ a `tests/integration/mutants.py` hunk**. e2m75 is re-anchored on the post-0023 line `    "infrx.acknowledge_dispatch(jsonb)", "infrx.admit_ready(jsonb)",\n` and inverted: the mutant puts `"infrx.admit(jsonb)"` back, which must fail `L3-LOGIN-infrx_runtime-functions`. It is re-described as "0023 revoked admit; re-adding it to the expected set must fail its row". `git apply --check` is clean on union `9d61d1e1`'s three files. |
| 0-DR-CM-1 (runtime still calls the revoked doors pre-W5) | **not a lane defect: merge gate, verified satisfied on the union** | The regression is `test_composition_pg::test_f_base__…_dedicated_login`. It fails on the branch alone and passes on union + door-revoke (both runs below). |
| 1-DR-2 (0023 breaks the runtime login on a tree without W5; the textual merge is clean) | **not a lane defect: merge gate, verified satisfied on the union** | Same regression and runs. The ORDER note in 0023:21-23 is unchanged. A Docker-free static guard was skipped because the composition test already fails in the wrong order; add one if `tests/d` ever runs without Docker in a merge gate. |

### Commands (fix round)
| cmd | tree | exit | result |
|---|---|---|---|
| `git apply DOOR-REVOKE-wiring.patch` (v1), then `.venv/bin/python -m pytest -q tests/integration/test_run.py -k anchor_occurs` (repo root) | union `9d61d1e1` + `acc53094` | 1 | **fails before**: `stale anchors (id: occurrences found): {'e2m75': 0}`, 1 failed |
| same with the v2 `mutants.py` hunk added | same | 0 | **passes after**: 1 passed |
| `pytest -q tests/integration/test_run.py tests/integration/test_harness.py tests/integration/test_services.py` (v2 applied) | same | 0 | 81 passed, 24 skipped (layer 2 needs the e2 stack) |
| lane-local e2m75 proof (scratch script). It applies 0001-0023 to a fresh DB on `INFRX_D_TASK=revoke` PG 55459 and runs pgstate's own `L3-LOGIN-infrx_runtime-functions` SQL as `postgres`, once with the v2-patched `pgstate` and once with e2m75's `before→after` applied in memory. Extension-owned functions are excluded, as `tests/d/checks_reads.py:507-512` does, because the plain image keeps pgcrypto in `public` | same | 0 | patched: **PASS** (exact set, nothing missing or extra). e2m75: **FAIL (killed)**, expected-but-absent `['infrx.admit(jsonb)']` |
| `INFRX_D_TASK=revoke pytest -q tests/d/test_composition_pg.py tests/d/test_reads.py tests/d/test_ready.py tests/d/test_schema_postgres.py tests/g/test_composition.py` | union `9d61d1e1` + `acc53094` + v2 | 0 | **101 passed, 3 xfailed, 0 failed** (includes the dedicated-login composition test) |
| same | branch head `acc53094` alone | 1 | 100 passed, **1 failed**, 3 xfailed. The one failure is `test_composition_pg::…dedicated_login`: `InsufficientPrivilege: permission denied for function admit`. This is the W5 merge gate by design |

Not run by this lane: e2m75 as a real layer-2 mutant. It needs the `e2` compose stack
(55500+, `infrx-e2-*`), which is outside this lane's task-local allocation (LANE-RULES 3).
Coordinator/union command after applying v2:
`apps/infrx-api/.venv/bin/python tests/integration/run.py --layer 2 --keep --no-mutants`, then
`apps/infrx-api/.venv/bin/python tests/integration/mutants.py --layer 2 --only e2m75`
(expect `killed`), then tear the stack down.

### Wiring requests (supersede the v1 list)
- **W-DR1 v2** (`DOOR-REVOKE-wiring-v2.patch`: `tests/integration/pgstate.py`,
  `tests/integration/test_harness.py`, `tests/integration/mutants.py`). Apply AFTER D10F's W4,
  which is already on union `9d61d1e1`. Do NOT also apply v1: v2 contains v1's two hunks
  unchanged.
- **W-DR2**: unchanged.
- **ORDER (0-DR-CM-1, 1-DR-2)**: merge `codex/door-revoke` only into `codex/wave4b-union` at
  or after `9d61d1e1`, never directly into `claude/consumer-v1` ahead of W5. Deploy 0023 only
  after the W5 gateway and worker are the running release. Before rolling back to a pre-W5
  runtime, apply 0023's rollback grant. After the union merge, re-run
  `INFRX_D_TASK=<union> pytest -q tests/d/test_composition_pg.py`; the dedicated-login test
  must pass.

### Estimate (remaining, coordinator)
optimistic 0.25 h, likely 0.5 h, pessimistic 1 h; confidence medium. Basis: merge into the union
(clean, measured), apply v2, then run one e2 layer-2 stack for e2m75 and the L3 RLS row.
