# E3L-BIND: the eight LAB-PUBLISH cells bound to the real L3, plus the full `make lab-operate` run (`bab9b18`)

Lane e3l-bind (LW3), branch `codex/w5-e3l-bind`, base `49393c56`. The code head of the gate run is
`bab9b180`. It is `3d68a1fb` plus the l11 world's diagnostics fix and attempt 1's raw evidence.
The oracles are LAB-PUBLISH, LAB-ACCESS and SPLIT-CONTRACT. The tasklocal key is `e3l`
(compose block 57000-57099, PostgreSQL 57032). This is **not a gate acceptance**: the gate is
**FAIL** on two cross-lane findings (E3L-F1 and E3L-F2 below). Every other case PASSes on real
services.

## What is bound, and to what

The cells run against the real stores on the e3l stack's fresh clone per case (every migration
through 0043):

- `LabControl` over `PgControlStore` (0032).
- A3's `PgRegistry` and `PgCatalogDirectory`.
- L2's `LabAccess` over `PgAccessStore`.

For l10 and l12 the cells also use **R186's control factory**
(`infrx.lab.control.app:create_app`), run as its own process under uvicorn
(`tests/integration/lab_operate/control_box.py`) on the e3l control port 57003, which has spares.
The factory's own `INFRX_LAB_*` settings point at the clone.

There are two stand-ins, and neither of them decides an oracle:

- **`EngineSmoke`**: the dev smoke passes when the controlled engine answers `/v1/models`. It
  stands in because WR-L3-2's engine smoke adapter is not wired, and the factory's `NoEngine`
  answers 503.
- **The session verifier** (`lab_world.sessions`): GoTrue's `GET /auth/v1/user` over HS256
  tokens of the stack's PostgREST secret. It echoes the token's role as `aud`/`role`, so
  `GoTrueSessions`' own role check is what refuses anon and service_role tokens. It stands in
  because the stack runs no GoTrue.

Operator actions (approve, rollback, price_dev) are in-process `LabControl` calls under an
`OperatorSession` principal `operator:e3l`. The control routes expose none of them.

The world adds `ADMIN_A` and `ADMIN_B` (administrators of A and B) to `seed_lab`.

| id | case(s) | bound to | result at `bab9b180` |
|---|---|---|---|
| l02 | `a_provider_created_dev_revision_never_reaches_app_discovery`: DEV_A registers, validates, the operator prices it internally, DEV_A issues a provider_dev key (row: audience provider_dev, provider A, its dev endpoint). Alpha's `/v1/models` is unchanged (less `availability_as_of`). Alpha gets 404 `not_found` on `nemostation/e3l-l02-dev` and on `alias@label`, with no job row. The catalog resolves it for provider_dev on its endpoint and never for a consumer | LabControl + gateway process | **PASS** |
| l03 | four bad registrations are refused with no row written: a moving tag, `evil/runner@sha256`, a custom schema (all `InvalidRequest`), and B's model (`NotFound`). DEV_A in B's workspace gets `NotFound`. The valid registration returns True once, then False, then `Conflict` for another row under its id | LabControl / PgRegistry | **PASS** |
| l04 | a developer's proposal is `Forbidden`. B's administrator gets `NotFound` in A's workspace and naming A's revision in B's own. The proposal alone moves no listing. The operator's approval makes listing 2 on the proposal at a NEW card `rc_e3l_l04`: 300/900, `approved_by = operator:e3l`, not provisional. The audit has `lab_propose` by ADMIN_A and `lab_publish` by the operator (versions 1 to 2, card). The seed card is untouched | LabControl / 0032 | **PASS** |
| l05a | `app_discovers_and_serves_the_published_revision`. Before the runtime roll, the App fails closed: the alias is unlisted, and a call gets 400 `invalid_request` with nothing admitted (R69). After the roll (worker and gateway restarted on `ACTIVE_RATE_CARD_VERSION=rc_e3l_l05`), `/v1/models` lists the published deployment, serving revision and card at 300/900. Alpha's call is admitted on exactly those pins, succeeds, settles once, and `conserved` holds | L3 + the App box | **PASS** |
| l05b | `discovery_reports_the_listing_version_it_serves`: `/v1/models` says `listing_version: 1` for the deployment that listing 2 serves | gateway `/v1/models` | **FAIL = E3L-F1** |
| l06 | alpha's async job is accepted on R2 (listing 2) with no worker running. The operator rolls back to version 1: listing 3 lists R1 at the seed card, with a `lab_rollback` audit (2 to 3). The runtime rolls back to the seed card and the worker starts. The queued job runs once on R2's pins and settles once at R2's card. A new call routes to R1 at R1's card. `conserved` holds | L3 + the App box | **PASS** |
| l09 | (1) Two operators approve two proposals against listing 1, held on 0032's advisory lock until both wait: one Listing v2 and one `StateConflict`, and the loser stays `proposed_public`. (2) A stale-fence rollback gets `StateConflict` and moves nothing. (3) Two racing rollbacks give one v3 and one `StateConflict`. (4) A publisher's backend is terminated while its audit insert waits on a held `lab_control_events` lock, after its CAS wrote listing 4 in its transaction. The client sees `AdminShutdown`, and no listing, card, state move or audit is left. The retry on the same fence publishes v4 once, with one audit row | LabControl / 0032 on real PG | **PASS** |
| l10a | `a_control_service_restart_mid_operation_loses_nothing`: the factory process is SIGKILLed after `PgControlStore.propose` committed and before it answered (`control_box` hold point, marker). The client's answer is lost (transport error). App inference serves and settles once during the outage. After the restart, the Lab lists the proposal exactly once as `proposed`, with its audit row and no listing moved. The operator's approval then publishes it (v2), and the Lab lists it `approved` | factory process + LabControl | **PASS** |
| l10b | `a_retry_after_a_lost_answer_proposes_once`: the Lab's retry after the restart opens a **second** `proposed_public` proposal of the same dev revision | factory process | **FAIL = E3L-F2** |
| l12 | on the running factory, 7 control operations (GET models/deployments/proposals/aggregates; POST register, deployments/{id}/smoke, proposals) are refused for: alpha's /v1 key as the bearer, the key in the query, and anon and service_role tokens naming alpha. All of these get 401 `unauthenticated`. Alpha's signed-in session gets 403 `denied`, and nothing is written. Premises: the verifier judged at least 21 tokens; DEV_A reads A's proposals (200) and not B's (404); ADMIN_A proposes (201) | factory process | **PASS** |

l01, l07, l08 and l11 (the L1/L2 cells, unchanged apart from l11's diagnostics) PASS in the same run.

## E3L-F1 (l05b): discovery reports a constant listing version

`infrx/gateway/routes/models.py:62-64`:

```python
# ponytail: the catalog port does not return the listing version resolution landed on
# (D10 wiring); one served model is one listing.
LISTING_VERSION = 1
```

After a Lab publication, the entry for listing 2's deployment says `listing_version: 1`, which
contradicts its own `deployment_revision_id`. The same is true after a rollback (listing 3).
The raw is `E3L-raw-bab9b180/cases/test_l05_discovery*`. Owners: G7 (`/v1/models`) and A3/D10
(the catalog port should return the listing version resolution landed on).
`KNOWN_FAIL` in `mutants.py` keeps it out of the stack list's pristine baseline, as E8L did,
until it is fixed.

## E3L-F2 (l10b): a proposal retried after a lost answer is proposed twice

`LabControl.propose` mints a new `deployment_revision_id` on every call, and 0032's
`lab_control_propose` refuses only an id collision. A client whose answer was lost to a crash
(or a timeout) and that retries therefore opens a second `proposed_public` revision of the same
dev source, and the Lab lists two open proposals.

Measured: `[('edad09e2…', 'proposed_public'), ('5603688d…', 'proposed_public')]`.

Owners: lab-sql and L3. One fix is an additive migration: refuse `state_conflict` (or return the
open one) when the source already has an open `proposed_public` proposal of its serving version
on that prod endpoint. The other is an idempotency key on the route and the operation.
`KNOWN_FAIL` as above.

## Observations (not FAIL)

- **O1, R69 and Lab publication.** A publication at a new card unlists the alias and refuses
  calls with 400 until the operator restarts the App on that card (`ACTIVE_RATE_CARD_VERSION`).
  This is fail-closed and never serves at an unapproved card (l05a asserts it). It is also an
  availability gap: every publication or rollback is a coupled runtime roll (l05, l06 do it).
  Also, a gateway refuses to start on a card the current listing does not name. This was seen
  when a mutant made the rollback relist R2, so l06 now judges the rollback before the roll.
- **O2, an untyped database error.** `PgControlStore` surfaces psycopg's `AdminShutdown` for a
  terminated backend. It is untyped: the route renders it as 503 through `guarded`, but
  in-process callers see a psycopg class.
- **O3, route halves that cannot run on this base.** Some route halves are not bound because
  their dependency is not merged:
  - `POST register` and `GET models`/`deployments` on the factory need lab-sql's `ControlReads`
    (WR-LSQ-9, on `codex/w5-lab-sql-lw4 72af2763`). `Operations(l3, store)` passes the store as
    `reads`, so these would raise `AttributeError` and render as 503.
  - `POST …/smoke` needs WR-L3-2.

  l12 still proves these routes refuse before any read. Rerun after WR-LSQ-9 and WR-L3-2 merge:
  `apps/infrx-api/.venv/bin/python tests/integration/lab_operate/runner.py --out <dir> --only l03,l12`
  with a route-registration case added.
- **J01/J02 (the Lab App journey on the real L3): NOT RUN.** `apps/lab/tests/l/ui/journey.test.ts`
  drives `FakeControl`, and WR-LAB-API-2's composition (`codex/w5-composition-2`, bd45a25e /
  39426585) is not on this base. The rerun after it merges (and after WR-LSQ-9 and WR-L3-2) is:
  `cd apps/lab && INFRX_D_TASK=l4 pnpm test -- tests/l/ui/journey.test.ts` against the real L3
  control adapter on l4 (57503).

## Real runs (`make lab-operate`, absolute `--out` under `research/plan/evidence/e/`)

| Run | Head | Result |
|---|---|---|
| dev runs on a kept stack (scratch) | `f14e7142`..`b453cded` | Each cell was bound and run until green. The first l02 run failed on `availability_as_of` (a moving clock; the world now projects it out). The first l05 run showed E3L-F1 and O1. l10b showed E3L-F2. Full scenario pass at `b453cded`: 20 passed, 2 failed (F1, F2) |
| attempt 1: `make lab-operate` at `3d68a1fb` | clean before the run | gate FAIL: l04 and l05a INVALID[harness] (fake vLLM 57080 bind collision); l11b read as FAIL although its log shows `EADDRINUSE 127.0.0.1:57070` (a harness collision; `198e594d` now reports the log so it classifies INVALID). Raw: `E3L-raw-3d68a1fb/` |
| attempt 2 at `bab9b180` | clean | gate FAIL: l03 and l04 INVALID[harness] (57080 collision), l05b and l10b FAIL. The raw verdict was lost to a scratch-copy mistake on my side; the make log is kept at `E3L-raw-bab9b180/attempts/attempt2-make-lab-operate.log` |
| attempt 3 at `bab9b180` | clean | BLOCKED: `services: port bind failed twice (ephemeral-range collision, R-c)` (`attempts/attempt3-blocked-*`) |
| **attempt 4 at `bab9b180`** | **clean before the run** (`pre-run-git.txt`) | **gate FAIL (exit 1): 20 passed, 2 failed in 136 s** (l05b = E3L-F1, l10b = E3L-F2). Every other case PASSes. Cells: LAB-ACCESS **PASS**, LAB-PUBLISH **FAIL**, SPLIT-CONTRACT **FAIL** (through l05, which carries it). Lab build id `n6tyTkWHcCLyo41DZI-sj`. Raw: `E3L-raw-bab9b180/` (verdict.json, scenarios.xml/log, cases/, lab-build.log, make-lab-operate.log) |

About the pins: `pins.dirty: true` in attempt 4's verdict is the run's own `--out` directory,
which is untracked while the run writes it. `pre-run-git.txt` shows an empty `git status`
immediately before `make`. The images are supabase/postgres `sha256:7768d0d1…`, valkey
`sha256:d2e18f34…`, clickhouse `sha256:87e0a5b7…` and pgsty/minio `sha256:b6bfe723…`
(`verdict.json` `pins.images`); the base is `49393c56` and the head `bab9b180…`.

The collisions (57080, 57070, provisioning) are WR-E3L-2: 57000-57599 is still not in
`ip_local_reserved_ports` (`55432-55599,56379,56700-56999,…`), and host load was around 11 with
about 4,700 sockets during these runs.

## Checks

| Command | Exit | Result |
|---|---|---|
| `python -m pytest -q tests/integration/lab_operate/test_e3l_runner.py tests/integration/lab_operate/test_mutants.py` (no stack) | 0 | 17 passed, 1 skipped (empty stack parametrisation without INFRX_MUTANTS) |
| `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e3l python -m pytest -q tests/integration/lab_operate/test_mutants.py` on a kept stack at `ad170e30` (code = `bab9b180`) | 0 | **45 passed in 606 s: 12 layer-1 and 29 stack mutants killed**, list checks (`well_formed`, `every_case_is_covered_by_a_mutant`), 2 self-tests (`E3L-raw-bab9b180/e3l-mutants-all.log`). Two earlier attempts: the pristine baseline was broken by the 57080 collision (`attempts/mutants-*`; the debug log shows the `EADDRINUSE`). No survivor in any attempt |
| `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/i/lab` | 0 | **17 passed** (a first run during the kept-stack mutant list failed 3 on a 57095/57096 bind collision, the e3l block's I2L edge ports; green alone) |
| `python -m pytest -q tests/integration --co` | 2 | 608 collected, 3 errors. **Pre-existing on the base**: `import file mismatch` between the four `test_mutants.py` basenames (lab_evaluate/observe/rollout/operate; this lane adds no test file). With `--import-mode=importlib`: **631 collected, 0 errors** |
| `INFRX_D_TASK=l3 make api-test` (2 h 02 m, concurrent with this lane's runs and other lanes) | 2 | 6414 passed, 129 skipped, 9 xfailed; **50 failed + 8 errors, all shared-lock/foreign, none in this lane's paths**: 7 `tests/d/test_outbox_relay.py[valkey]` (`HarnessBusy: another run holds /tmp/infrx-d2-valkey-55463.lock`), 8 `tests/i` i8-stack errors (`BlockingIOError` on the i8 locks), 43 `tests/i/test_mutants.py` whose pristine baseline includes those i8 cases (`E3L-raw-bab9b180/api-test.log`); the same pattern as `E3L-d1b0f21`'s run |
| `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/i` (rerun alone) | 0 | **349 passed, 4 skipped, 1 xfailed** (clears the 8 errors + 43 mutant failures; incl. `tests/i/lab`) (`rerun-tests-i.log`) |
| `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_outbox_relay.py` (rerun alone) | 1 | 13 passed, **7 failed [valkey], all `ForeignContainer: infrx-d2-valkey exists and is not this checkout's`** (another checkout's container; not touched). An l3 attempt was refused `HarnessBusy` - l3 is held by another lane's `l3-pg-pristine` mutant run, so l4 was used (`rerun-outbox-relay.log`) |
| e3l teardown (`run.backend_teardown` + `run.teardown`) | 0 | removed infrx-e3l-{clickhouse,postgres,s3,valkey}; no e3l container, volume, network or process left |

Failing seam first: before binding, all eight cells were `NOT RUN[L3,L4] L3 merged; the case is
not yet bound to its control port (E3L-BIND)` (dev run 1, `--only l02`, and the attempt of
`E3L-d1b0f21`). Each bound case was then run red and green on the kept stack: l02 red on the
moving clock, l05 red on F1, l10 red on F2. Each is killed by its mutants below.

### Stack mutants added (every new case named; they die by assertion)

These are in addition to the 12 existing mutants.

| Case | Mutants |
|---|---|
| l02 | `st_private_resolves_for_consumers` (extended to the bound case) |
| l03 | `st_moving_tag_registers`, `st_any_runtime_registers`, `st_any_schema_registers`, `st_foreign_model_registers` |
| l04 | `st_developer_proposes`, `st_card_unattributed`, `st_publish_audit_actor` (0032), `st_card_input_rate_misfiled` (0032, also l05) |
| l05a | `st_unapproved_card_listed` |
| l06 | `st_rollback_relists_the_current` (0032) |
| l09 | `st_publish_without_cas`, `st_rollback_without_cas` (0032) |
| l10a | `st_restarted_proposal_misread` (Operations), `st_control_never_crashed` (the drill's premise) |
| l12 | `st_any_token_role_is_a_session`, `st_unshaped_bearer_passes`, `st_consumer_session_not_denied` (lab_auth) |

Removed: the layer-1 `unbound_case_runs` mutant and the `waits`/`unbound` machinery, because
every L3/L4 case is now bound. Deviation: the commits for l06 (`8c6b9f31`) and l09 (`2bc39b06`)
declared their mutants, but the anchors did not match, so the mutants were added in `fb582d59`
(the coverage check caught it).

## Changed paths (owned only)

- `tests/integration/lab_operate/`:
  - `lab_world.py`: ADMIN_A/ADMIN_B, the L3 helpers, `EngineSmoke`, the session verifier,
    `ControlService`.
  - `control_box.py` (new).
  - `scenarios_publish.py`: the eight cells bound.
  - `scenarios_split.py`: l11's start-failure diagnostics.
  - `runner.py`: REQUIRED gains l05b and l10b; BASE.
  - `mutants.py`, `test_mutants.py`.
- `research/plan/evidence/e/E3L-BIND-bab9b18.md`, `E3L-raw-3d68a1fb/`, `E3L-raw-bab9b180/`.
- `research/plan/evidence/coordinator/updates/E3L-*.json`.

## Wiring requests

None new. The Makefile `lab-operate` target and the api-mutants line already exist.
**WR-E3L-2 is still open**: reserve 57000-57599 in `ip_local_reserved_ports` (user/root, P-21
extension). Every harness collision above is in that block.

## Proposed rulings (coordinator numbers)

1. **E3L's LAB-PUBLISH binding.** The cells bind in-process to `LabControl` on the real stores.
   The route cells bind to R186's factory as a process. The only stand-ins are the engine smoke
   (until WR-L3-2) and the session verifier (the stack has no GoTrue), and neither decides an
   oracle. The route halves that need `ControlReads` (WR-LSQ-9) and the Lab App journey (J01/J02
   on the real L3) remain NOT RUN until those merge.
2. **R69 × Lab publication (O1).** A publication or rollback that changes the alias's card is
   served only after the operator's runtime roll onto that card. Until then the alias is
   unlisted and refused, never served at another card. I2L's runbook pairs every approval or
   rollback with the roll (or a later ruling lets the runtime accept any card its listing names).
3. **Idempotent proposal (E3L-F2).** A publication proposal is one per dev revision while it is
   open. A retry answers the open proposal (or `state_conflict`), never a second one.

## Open issues

- E3L-F1 (G7 plus A3/D10), E3L-F2 (lab-sql plus L3): the gate FAILs until they are fixed; then
  rerun `make lab-operate`.
- WR-LSQ-9, WR-L3-2 and the composition-2 merge: then bind the route halves and J01/J02.
- WR-E3L-2 (port reservation).
- The pre-existing `tests/integration --co` basename collision.

## Estimate (remaining for E3L)

- optimistic 1 h, likely 3 h, pessimistic 6 h
- confidence: medium
- basis: E3L's 3/6/12 second half. This slice took about 3.5 h, including 4 gate attempts under
  host load. What remains is a rerun after the F1/F2 fixes plus binding the ControlReads route
  halves and J01/J02 (about 1-2 cases each) once WR-LSQ-9 and composition-2 merge.

## Rulings

Numbered R203–R205 in `research/plan/08-contracts-v1-encoding.md` §10 at the e3l-bind merge on
`codex/w5-merge-25` (this file's "Proposed rulings" above, in order). LAB-OPERATE-LOCAL is not
accepted at this integration: E3L-F1 routes to lane e3l-f1, E3L-F2 to lab-sql-lw6, then a rerun
that also binds J01/J02 and the ControlReads route halves now on the tip.
