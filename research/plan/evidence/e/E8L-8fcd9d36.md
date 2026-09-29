# E8L continuation (lane lab-rollout-3, LW6): WR-E8L-3 real ControlReads, k09 exits 0 on the
# real process, k10's read half bound, full run k01-k07 PASS

Task: E8L (tasks.json:3438). Branch `codex/w5-lab-rollout-3`, worktree
`.claude/worktrees/codex-w5-lab-rollout-3`, base `57a779af` (merge #35: rollout-identity,
R216, WR-E8L-9/10; merge #30: `pilot.control_serving` composes `PgControlStore`'s real
ControlReads; 0045-0051 local). Measured head `8fcd9d36`. Tasklocal key `e8l` (compose
57400-57499, PostgreSQL 57432); `INFRX_D_TASK=e8l` for api-test. Everything local: nothing
touched hosted Supabase, the pilot box, AWS/SSM/S3, Vercel or secrets; no migration, no
product code, no `apps/` file changed. Continues `E8L-d68a1ddf.md` and
`research/plan/evidence/r/ROLLOUT-IDENTITY-6d36750.md`.

## Commits (one per step, never amended)

| commit | step |
|---|---|
| `bae39c33` | (1) WR-E8L-3: the world's `Reads` stand-in -> real `PgControlStore` on 0044's `infrx_lab_control` login |
| `7f9f57d9` | (2) k09 rebound: the real emergency-rollback process exits 0 and moves a promoted alias back; restart is a no-op |
| `8fcd9d36` | (3a) k10's read half bound to 0048's `lab_releases_in`; UI half stays NOT RUN |
| (this commit) | (3) evidence: raw run, mutant log, this file, update JSON |

## (1) WR-E8L-3 - DONE

`lab_world.Lab.serving()` now composes `Serving(LabControl(...), PgControlStore(connector(
self.control, set_role=False)), ...)`: the real store (`infrx.state.lab_control`, the one
`pilot.control_serving` composes) on the control service's own login. `self.control` is
`runtime_dsn(DATABASE, "infrx_lab_control")` (the existing E3C WR-4 helper, now taking the
role: LOGIN + a fresh random password on this namespace's cluster only). The `Reads` class is
deleted.

Fail-first (recorded, kept stack, world unchanged): new case
`test_k06_the_worlds_alias_read_is_the_real_control_store_on_its_login` (R207 oracle: an
isolated alias `nemostation/e8l-moved` listed v1 on endpoint A, v2 on endpoint B; A must answer
nothing) FAILED on the stand-in:
`{'moved_off': 'nemostation/e8l-moved', ..., 'reads': 'Reads'}` - `1 failed, 1 passed`.
After the swap: `2 passed` (k06 both cases). A login refusal is recorded as the error's type
name, so a missing grant dies as an assertion.

New stack mutants naming it (both killed): `st_alias_answers_a_superseded_listing`
(`PgControlStore._ENDPOINT_ALIAS` loses R207's current-listing clause) and
`st_control_login_lacks_the_listings_read` (0044's `grant select on infrx.catalog_listings to
infrx_lab_control` removed - proves the read runs on the login).

## (2) k09 - the real process now converges the alias

Fail-first (recorded): the unchanged k09 (expecting exit 1 on `NoControlReads`) failed on the
tip: `exits were 0, 0` - merge #30 satisfied WR-E8L-7.

Rebound: launch with the alias's listed deployment as baseline (L3's ref form, as k06/WR-E8L-9),
promote `serving_2` (`lab.promote(..., 9)`), run `python -m infrx.lab.workers rollout
emergency-rollback --policy-ref <ref> --reason ...` as a real OS process twice. Measured in the
full run (`E8L-raw-8fcd9d36/cases/test_k09_*/process.json`):

| | exit | alias listing (version, deployment) |
|---|---|---|
| before | - | (4, `c0000004-...0004`) baseline |
| promoted | - | (5, `00000de8-...0009`) candidate |
| first invocation | **0** (stderr empty) | (6, `c0000004-...0004`) - moved back to baseline |
| second (restart) | **0** (stderr empty) | (6, `c0000004-...0004`) - unchanged |

One D9 decision (`rollback`/`rolled_back` by `LAB_OPERATOR_ID`) after both. The pass loop stays
`NOT RUN[WR-R2-3]` (`_rollout` still refuses by name on the tip): rerun
`apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py --out <dir> --only k09`
once composition-5/WR-R2-3 lands the pass loop.

Mutants: new `st_process_reads_nothing` (`pilot.control_serving`'s `PgControlStore(connect)` ->
`PgControlStore(None)`: the real process crashes, exit 1) names k09; `st_lost_race_raises`
still names it (second exit would be 1).

## (3) k10 - read half bound; UI half NOT RUN

`pilot._lab_2` on the tip still composes `LabReleases(sessions, access)` with no `records` port
(no `ReleaseRecords` implementation exists outside `apps/lab/tests/r/backend.py`'s test-only
listing): `/lab/v1/releases` answers 503. What the tip supports is the store listing (0048,
merge #34): new stack case `test_k10_the_release_listing_reads_d9s_rows_and_r2s_latest_verdict`
(`PgReleaseStore.releases_in`: a launched release listed running with its endpoint/provider and
no decision; after an emergency rollback listed only under `rolled_back`, fence = D9's, latest
decision `rollback` by the operator) - PASS. Mutant `st_listing_ignores_the_state` (0048's state
filter -> `true`) names it. The UI case keeps the scenario NOT RUN[lab-ui-swap]: rerun
`... runner.py --out <dir> --only k10` once the records/proposal ports are composed
(WR-R4-1 lab-sql half, WR-R4-2) and `apps/lab/tests/e2e/rollout/` exists.

## (3) Full real run: `make lab-rollout` at `8fcd9d36`

`E8L-raw-8fcd9d36/` (verdict.json, scenarios.xml, scenarios.log, cases/, mutants/). Fresh stack,
torn down by the run. 73.4 s; pytest `20 passed, 3 skipped in 53.74s`. `pins.dirty: true` =
only the evidence directory being written at run time.

| id | status | rerun |
|---|---|---|
| k01 | PASS | |
| k02 | PASS | |
| k03 | PASS | |
| k04 | PASS | |
| k05 | PASS | |
| k06 | PASS (plain cell; incl. the new WR-E8L-3 case) | |
| k07 | PASS | |
| k08 | NOT RUN[P-08] no GPU target, no measured parity | `apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py --out <dir> --only k08` |
| k09 | NOT RUN[WR-R2-3] pass loop only; real process 0/0 with the alias moved | `... runner.py --out <dir> --only k09` |
| k10 | NOT RUN[lab-ui-swap] UI half; read-half case PASS | `... runner.py --out <dir> --only k10` |

Cells: ROLLOUT-PIN NOT RUN (k10), ROLLOUT-RECOVER NOT RUN (k09), OPT-PARITY NOT RUN (k08).
**Gate NOT RUN** (exit 3): no FAIL anywhere; every non-PASS is a named wait. Pins: postgres
`supabase/postgres@sha256:7768d0d1...`, valkey `valkey/valkey@sha256:d2e18f34...`, clickhouse
`clickhouse/clickhouse-server@sha256:87e0a5b7...`, s3 `pgsty/minio@sha256:b6bfe723...` (full
digests in verdict.json). Note: `runner.BASE` still reads `b2906856` (the base the runner was
built on); the real base of this run is `57a779af`.

## Commands

| command | exit | result |
|---|---|---|
| `runner.py --keep --only k06` (new case, stand-in world) | 1 | k06 FAIL: fail-first above (`1 failed, 1 passed`) |
| `runner.py --reuse --keep --only k06` (real store) | 3 | k06 `2 passed` |
| `runner.py --reuse --keep --only k09` (k09 unchanged) | 1 | FAIL `exits were 0, 0` (k09 fail-first) |
| `runner.py --reuse --keep --only k09,k06` (k09 rebound) | 3 | `2 passed, 1 skipped` (k09 NOT RUN[WR-R2-3] after its real assertions) |
| `runner.py --reuse --keep --only k10` | 3 | `1 passed, 1 skipped` |
| targeted stack mutants (`st_alias_*`, `st_control_login_*`, `st_process_*`, `st_lost_race_raises`, `st_listing_*`, `a_required_case_renamed`, list checks) | 0 | all killed |
| **`make lab-rollout`** (fresh stack) | **3** | **gate NOT RUN: k01-k07 PASS, k08/k09/k10 NOT RUN** |
| `runner.py --keep --out <scratch>/kept` (stack for the list) | 3 | same verdict |
| `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e8l pytest -q -rA test_mutants.py` (kept stack) | 0 | **58 passed in 357.93s**: 38 stack + 16 layer-1 mutants killed, 0 survivors (incl. `st_converge_by_full_ref`, `st_serving_ref_digest_drifts`), 2 self-tests, well-formed, every case named (log: `E8L-raw-8fcd9d36/mutants/`) |
| `runner.py --reuse --only k08` (teardown) | 3 | `removed: [infrx-e8l-clickhouse, -postgres, -s3, -valkey]`; no `e8l` container left |
| `pytest -q tests/integration/lab_rollout` (layer-1, no stack) | 0 | 17 passed, 1 skipped |
| `uvx ruff check --line-length 100 tests/integration/lab_rollout` | 0 | all checks passed |
| `INFRX_D_TASK=e8l make api-test` | (2) | **6650 passed, 7 failed, 167 skipped, 9 xfailed in 5309s**; all 7 = `tests/d/test_outbox_relay.py::*[valkey]`: `ForeignContainer: infrx-d2-valkey exists and is not this checkout's` (foreign leftover, same as ROLLOUT-IDENTITY's run). pytest printed its summary then hung at exit polling 3 connections to `infrx-lab-on-postgres` (127.0.0.1:57537, a container labelled for `codex-w5-lab-deploy-prep`); I terminated my own pytest after ~8 min, so make's exit code was not captured (would be 2). No i8-lock NOT RUN skips seen. |
| `make -n lab-rollout` | - | `apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py --out <abs>/research/plan/evidence/e/E8L-raw-8fcd9d36` |

Foreign leftovers (not touched): `infrx-d1-postgres`, `infrx-d2-postgres`, `infrx-d2-valkey`,
`infrx-t2f-{postgres,s3,clickhouse}`, `infrx-b3-postgres` (Created), 3 `infrx-e5l_*` volumes,
and `infrx-lab-on-postgres` (another lane's).

## Changed paths (owned only)

`tests/integration/lab_rollout/{lab_world.py, mutants.py, runner.py, scenarios_pending.py,
scenarios_recover.py, scenarios_route.py}`, `research/plan/evidence/e/E8L-8fcd9d36.md`,
`research/plan/evidence/e/E8L-raw-8fcd9d36/`, the coordinator update JSON.

## Wiring requests

None new. Still open elsewhere: WR-R2-3 (the pass loop's read models; k09's loop), WR-R4-1
lab-sql half + WR-R4-2 (a `ReleaseRecords` adapter over `releases_in` and `PgReleaseProposals`
composed in `pilot._lab_2`; k10's UI), P-08 (k08). WR-E8L-3 and WR-E8L-7 are closed by this
run.

## Open issues

- `runner.BASE = "b2906856"` is stale in verdict pins (cosmetic; actual base recorded here).
- The api-test hang at exit on `infrx-lab-on-postgres` looks like a foreign container shared by
  key (not e8l); not investigated beyond the socket trace.

## Deviations

- The brief's "layer-1 case" for WR-E8L-3 is a stack case in the k06 scenario: R207's oracle
  needs PostgreSQL (as `E8L-d68a1ddf.md`'s identity case).
- Step (3) is two commits (3a code, 3 evidence).

## Estimate (remaining E8L)

Optimistic 1 h / likely 2 h / pessimistic 5 h, confidence medium; basis: each remaining cell is
one binding + one rerun once its dependency lands (this lane: ~3.5 h for three bindings, a full
run, the mutant list and a 90-min api-test).

## Verification log

- 2026-09-29: WR-E8L-3, k09 rebound (exits 0/0, alias moved), k10 read half; full run gate NOT
  RUN with k01-k07 PASS; mutant list 58/58; stack torn down. Local only.
