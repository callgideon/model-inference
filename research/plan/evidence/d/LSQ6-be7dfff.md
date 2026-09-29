# LSQ6: lab-sql LW6 (E3L-F2/R205, WR-R4-1's lab-sql half, WR-LSQ-C2A/C2B, WR-N4-3, WR-E8L-3, WR-DS5-1)

Lane `lab-sql-lw6`, branch `codex/w5-lab-sql-lw6`, worktree
`.claude/worktrees/codex-w5-lab-sql-lw6`, base `eb561d5f` (head of `codex/w5-merge-28`: lab-sql
LW5 merged, migrations 0027-0046 present, R207/R208 numbered). Code head `be7dfff3`. Tasklocal
key `dlab` (PG 57500, decoy 27500) for every real-PG run; `l3`/`r1`/`n3` were not needed (every
check ran on `dlab`). Nothing hosted, nothing touched outside this lane's owned paths; no App
file changed; foreign leftovers (`infrx-d1-postgres`, `infrx-t2f-postgres`, `infrx-d2-valkey`,
`infrx-e5l_*`) were seen and left alone.

This lane has no plan entry (wave5-plan.md has no `lab-sql-lw6` row): the brief is the
coordinator's dispatch notes plus §2 (global rules) and §5 COMMON/LW1 (the standing lab-sql
lane rules) of `wave5-plan.md`.

## Done (in brief order, one commit each)

### (1) E3L-F2/R205: `lab_control_propose` is idempotent per dev revision

Bug (`evidence/e/E3L-BIND-bab9b18.md` "E3L-F2"): `LabControl.propose` mints a NEW
`deployment_revision_id` every call, and 0032's `lab_control_propose` refused only an id
COLLISION. A retry after a lost answer (a crash, a timeout) therefore opened a SECOND
`proposed_public` revision of the same dev source (l10b: two open proposals of one
`source_revision_id`).

Fix, additive migration `0047_lab_control_propose_idempotent.sql`: before inserting, lock per
SOURCE revision (`pg_advisory_xact_lock`, 0032's alias-lock pattern) and look up the most
recent `lab_propose` audit row (`lab_control_events.after->>'source'`) whose resulting
deployment revision is still `proposed_public` - not `(endpoint_id, serving_version_id)`
alone, which the existing seed fixture (two unrelated `proposed_public` rows of one serving
version, seeded directly to exercise a publish-CAS race) would have wrongly matched. Found
this the hard way: the first cut kept the wrong key and broke
`check_a_proposal_comes_from_a_validated_dev_source`'s pre-existing assertions (which I then
updated to the new idempotent behaviour, since a same-source repeat is now answered, not
refused). Mirrored in `fakes.py` (scans `self.audit` for the source's open proposal) so the
fake-world unit tests exercise the same rule.

R205 picks the "answers the open proposal" reading (not `state_conflict`): the E3L l10b cell
(`test_l10_a_retry_after_a_lost_answer_proposes_once`) asserts only that the database keeps
exactly one `proposed_public` row of the source, not the retry's status code, and "return the
existing" matches every other idempotent RPC in this schema (`lab_judge_reserve`,
`lab_judge_begin_submit`).

Fail-first: `check_a_proposal_retried_after_a_lost_answer_proposes_once` (PG) and
`test_lab_control__a_proposal_retried_after_a_lost_answer_proposes_once` (fake, both `world`
params) reproduce the duplicate before the fix (confirmed by reverting the fakes.py change and
rerunning - "a retry opened a second proposal", assertion failed as expected) and pass after.
Added a genuine-concurrency case,
`check_a_proposal_retry_race_of_the_same_source_proposes_once` (two threads, released
together, racing the SAME source), because a sequential retry test cannot kill the
advisory-lock mutant.

### (2) WR-R4-1 (lab-sql half): a release listing with R2's latest verdict

`0039_lab_release.sql`'s `lab_release` reads exactly one release by an already-known
`policy_ref`. Neither the release-ui page (every release of a provider) nor the rollout pass
loop (WR-R2-3's "every running/rolled-back D9 release") can be served by that. Additive
migration `0048_lab_release_listing.sql` adds `lab_releases_in {provider_org_id, states?}`:
the provider's `lab_rollouts` rows (policy_id, endpoint_id, policy_ref, state, fence,
plan_digest, started_at) LEFT JOINed to the latest `lab_rollout_events` row that carries a
decision (null before one is made), optionally filtered by state.
`PgReleaseStore.releases_in` + `Release`/`Decision`-typed `ReleaseListing` wrap it.

Left to composition-4 (explicitly not SQL, per the brief): assembling R2's full `Plan`
(thresholds/horizon/budget/protocol - only its frozen `plan_digest` is stored, by design, so
thresholds cannot be loosened after launch), R1's `Live` aggregates and B2's stored
report/runs into the `Controller.step` call WR-R2-3 also asked for.

Fail-first: `check_releases_in_lists_the_providers_releases_with_r2s_latest_verdict` failed
"another provider's release leaked into the listing" when the provider filter was temporarily
replaced with `where true` (confirmed, then reverted).

### (3) The SQL halves of WR-LSQ-C2A, WR-LSQ-C2B, WR-N4-3 (+ WR-R2-3/WR-B3-3 notes)

Per `COMPOSITION-2-b790f17.md`'s "Wiring requests" section:

- **WR-LSQ-C2A** (`0049_lab_judge_runs_in.sql`): `lab_judge_runs_in {provider_org_id, states,
  limit}` lists a provider's judge runs in the given states (`submitted` for the judge role's
  collect pass, `ambiguous` for reconcile), oldest-`updated_at` first, bounded to `limit`; an
  empty `states` is `invalid_request` (never an unbounded scan). `PgJudgeLedger.runs_in`
  wraps it. Today "a run is collected by the door that submitted it" (the worker main's own
  ponytail note); this is the listing that lets `collect`/`reconcile` find work after a
  restart instead.
- **WR-LSQ-C2B** (`0050_lab_outbox_pending_kinds.sql`): `lab_outbox_pending` gains an
  OPTIONAL `kinds` filter (empty/omitted: every kind, the current one-outbox-for-all
  behaviour, unchanged). `PgLabDataStore.dispatch_pending(kinds=...)` threads it through.
  Since `lab_outbox_pending`'s whole body moves to 0050 (a `create or replace`, same as
  E3L-F2's propose), the pre-existing `d7_redelivered_early`/`d7_acknowledged_redelivered`
  mutants (anchored in 0029's copy of the same text) were re-filed onto 0050 - the E3L-F2
  lesson applied a second time: a mutant of the SUPERSEDED file's text is silently masked and
  never actually runs (`test_no_mutant_anchors_in_a_superseded_function_body` catches exactly
  this, and did, on the first pass).
- **WR-N4-3** (`0051_lab_import_jobs.sql`): a durable `lab_import_jobs` lease queue -
  `enqueue` (idempotent per import id, the route's own idempotency key), `claim` (outbox-
  shaped: oldest `queued`, or `running` past its lease, `for update skip locked`),
  `heartbeat`, `finish` (idempotent at the SAME terminal state, refused for a different one
  or a non-holding worker), `job` (read, own provider only). `PgLabImportJobs` wraps it. So
  `LAB_DATASETS` imports can run in the I5 pool instead of a gateway request's own
  `asyncio.create_task` (today's ponytail note in `lab_datasets.py`).
- **WR-R2-3**'s release half is (2)'s `lab_releases_in`; nothing further needed here.
- **WR-B3-3** (the `Deployer` and registry adapters): pure L3/composition work, no SQL
  surface - 0042's checkpoint ledger (`PgCheckpointLedger`) already exists and is unchanged.
  Nothing to add from lab-sql; still owned by L3/composition-4.

Fail-first for each: `check_runs_in_lists_this_providers_runs_by_state_oldest_first`,
`check_a_roles_relay_claims_only_its_own_kinds`,
`check_import_jobs_are_a_durable_claim_and_lease_queue` (the "a dead worker's lease was never
redelivered" IndexError-turned-assertion caught a real bug in the check itself on the first
mutant run, fixed to a clean list comparison).

### (4) WR-E8L-3: already covered by WR-LSQ-9/R207 - nothing left for lab-sql

`E8L-2334e6a.md`'s WR-E8L-3 asks for `ControlReads.endpoint_alias`/`listing_versions` on
`PgControlStore`. Checked `infrx/state/lab_control.py`: `provider_servings`,
`provider_deployments`, `endpoint_alias` and `listing_versions` are all already implemented
(landed via WR-LSQ-9, `0044_lab_control_reads.sql`, and refined by R207's ordering fix in
`0044`/lab-sql-lw5's endpoint_alias oracle), and `tests/d/test_l3sql_reads.py` already
exercises all four against real PG (`check_endpoint_alias_orders_across_aliases_by_time_...`,
`check_endpoint_alias_answers_nothing_for_an_endpoint_the_alias_moved_off`, etc.). Nothing
remains on the SQL/store side. What is still open is composition-side and out of this lane's
paths: `pilot.py`'s `lab_operations`/`control_serving` still construct `NoControlReads()`
instead of the real `PgControlStore` (WR-LAB-API-2c, already filed by an earlier lane), and
`tests/integration/lab_rollout/lab_world.py`'s `Reads` stand-in needs swapping for it at that
same merge (owned by lab-rollout/E8L, not lab-sql). No commit for this item.

### (5) WR-DS5-1: `PgLabDataStore`/`PgAccessStore` each carry a `restrictions` port

Before: `lineage.restrictions_of` fell back to `PgSampleRestrictions(store._connect)` for any
store lacking its own `.restrictions` - reaching into a private attribute of a store it does
not own. Added `PgLabDataStore.restrictions` (a `@property` constructing
`PgSampleRestrictions(self._connect)`) and simplified `restrictions_of` to the one-line
`getattr(store, "restrictions", None)` (the one-line change in `infrx/datasets/lineage/
__init__.py` the brief names).

Caught in review before it shipped: the Lab worker's `_datasets` composition
(`infrx/lab/workers/__main__.py`) passes a bare `PgAccessStore(connect)` as `reconcile`'s
`directory` - which has `_connect` but no `.restrictions` of its own, so dropping the
`_connect` fallback would have silently stopped 0041 writes reaching production reconcile
runs. Since `infrx/state/lab_access.py` is one of lab-sql's owned files too, gave
`PgAccessStore` the same `restrictions` property instead of filing a composition wiring
request - a same-file fix, not a behaviour change, and it needs no patch to
`infrx/lab/workers/__main__.py` at all.

`tests/n/lineage/mutants.py`'s `n3_portless_store_refused` updated to the new one-line body
(its old anchor, `return None if connect is None else PgSampleRestrictions(connect)`, no
longer exists). No new PG test needed: `test_n3_pg_selection_revocation_and_tombstones`
(real `PgAccessStore` reconcile, no `restrictions` kwarg) and the fake-world's
`test_n3_callers_without_the_port_still_deny_for_good` (COMPAT case, a store with neither) already
regress both paths and stayed green through the change.

## Checks (commands and results)

All on `INFRX_D_TASK=dlab` (or the key named) task-local PostgreSQL, this checkout.

| Command | Result |
|---|---|
| `INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d7_lab_data.py tests/d/test_d6j_judge.py tests/d/test_upgrade_lab.py tests/d/test_d9_release.py tests/d/test_l3sql_control.py tests/d/test_d7_units.py` | 60 passed |
| `uv run --frozen pytest -q tests/l/control/test_control.py -m "not pg"` | 14 passed |
| `INFRX_D_TASK=dlab uv run --frozen pytest -q tests/l/control/ -m pg` | 23 passed, 30 deselected (the fake-vs-pg split; the pg half runs on `PgControlStore`) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/l/control/test_mutants.py -k "not pg"` | 72 passed, 24 deselected (pg half needs Docker) |
| `INFRX_D_TASK=dlab INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_code_mutants_l3sql.py` | 66 passed, 0 survivors |
| `INFRX_D_TASK=dlab INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_code_mutants_d9.py -k release` | 30 passed, 0 survivors |
| `INFRX_D_TASK=dlab INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_code_mutants_d6j.py -k judge` | 42 passed, 0 survivors |
| `INFRX_D_TASK=dlab INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_code_mutants_d7.py` (full, 3 attempts; the first two caught and fixed a superseded-anchor gap and a check bug - see "Deviations") | **159 passed, 0 survivors** (6m31s) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/n/lineage/test_mutants.py` | 52 passed, 0 survivors |
| `INFRX_D_TASK=n3 uv run --frozen pytest -q tests/n/lineage/test_lineage_pg.py` | 2 passed (real `PgAccessStore`/`PgLabDataStore` `.restrictions`, unchanged behaviour) |

## Changed paths (owned only)

- `apps/app/supabase/migrations/0047_lab_control_propose_idempotent.sql`,
  `0048_lab_release_listing.sql`, `0049_lab_judge_runs_in.sql`,
  `0050_lab_outbox_pending_kinds.sql`, `0051_lab_import_jobs.sql`.
- `infrx/lab/control/fakes.py` (the propose path only, as the brief allowed).
- `infrx/state/{lab_access,lab_consent,lab_data,lab_rollout}.py`.
- `infrx/datasets/lineage/__init__.py` (the one `restrictions_of` line).
- `tests/d/test_{l3sql_control,d9_release,d6j_judge,d7_lab_data,d7_units,upgrade_lab}.py`,
  `tests/d/code_mutants_{l3sql,d9,d6j,d7}.py`.
- `tests/l/control/{mutants,test_control}.py`.
- `tests/n/lineage/mutants.py` (the one mutant WR-DS5-1 required).
- `tests/integration/test_harness.py` (the migration pin, all five added in one edit rather
  than five - see "Deviations").

## Wiring requests (for other owners; none applied)

- Everything already filed by `COMPOSITION-2-b790f17.md` and `E8L-2334e6a.md` for items (2)-(4)
  above (Plan/Live/B2 assembly into the rollout pass loop; the `Deployer`/registry adapters;
  `pilot.py`'s `NoControlReads()` swap; `lab_world.py`'s `Reads` swap) - none of it is SQL,
  and none of it is new: this lane adds nothing beyond confirming what's already open.
- No `Makefile`/`checks.py`/`EXPECTED_FUNCTION_CALLERS` change needed: every new RPC is a
  plain internal `infrx.*(jsonb)` function, closed by 0004's default privileges the same way
  every other Lab RPC since 0027 is (verified: none of them appear in
  `EXPECTED_FUNCTION_CALLERS` or `INFRX_CALLABLE` either); every touched mutant list
  (`test_code_mutants_{l3sql,d9,d6j,d7}.py`) is already in the Makefile's `api-mutants` line.

## Proposed rulings (coordinator numbers; run through R208)

1. **E3L-F2's reading (R205 already numbered; this confirms the choice).** A publication
   proposal is one per dev revision while it is open, identified by its `lab_propose` audit
   trail (never by `(endpoint_id, serving_version_id)` alone, which unrelated rows can share).
   A retry answers the open proposal.
2. **A role's outbox relay claims its own kinds only (WR-LSQ-C2B).** `kinds` is optional and
   additive; a role that never opts in keeps today's one-outbox-for-all behaviour.

## Deviations

- The migration pin at `tests/integration/test_harness.py` was updated once, listing all five
  new files together, rather than incrementally re-editing the same list five times across
  five commits; each commit's own migration is present at that commit either way since the
  full working tree existed before any commit was made.
- `apps/infrx-api/tests/d/test_upgrade_lab.py`'s new `test_the_lw6_upgrade_keeps_existing_rows_
  and_reruns` covers all five migrations (0047-0051) together as one upgrade test, following
  this file's own per-wave convention (`test_the_lw5_upgrade_...` etc. also cover a whole
  wave's files at once), and landed in commit (3) rather than split five ways.
- `check_a_proposal_comes_from_a_validated_dev_source`'s trailing assertion ("id reused" ->
  `state_conflict`) is now "a repeat call is idempotent" -> the same row: R205 makes ANY
  repeat of the same source idempotent, including the identical-id case that assertion
  originally tested; superseded by the new behaviour, not a regression.
- Two rounds of real-PG mutant fixes on `test_code_mutants_d7.py` (an unhandled `IndexError`
  turned into a clean assertion; a unit-test case with no covering CODE mutant given one) -
  both are named in the "Checks" table's final green run, not hidden.

## Open issues

None blocking. WR-E8L-3's remaining composition-side wiring (the `pilot.py` swap, the
`lab_world.py` swap) is owned by lab-rollout/composition-4, not lab-sql, and was already open
before this lane started.

## Estimate (remaining for this lane)

- optimistic 0 h, likely 0.5 h, pessimistic 2 h
- confidence: high
- basis: every brief item is done and green; the only remaining risk is a merge-time
  conflict with a concurrently-landing migration number (0047-0051 are the next five free
  numbers on this base as of `eb561d5f`; per rule 3, the actual number is assigned at merge).

Rulings: proposals 1 and 2 numbered R214 and R215 in `08-contracts-v1-encoding.md` §10 at the lab-sql-lw6 merge on `codex/w5-merge-34` (2026-09-29).
