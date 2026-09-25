# D10-FOLLOWUP: migration 0022, code head `4b462094`

| | |
|---|---|
| Task | D10 follow-up (program 22). W5 wiring request 3 (`fail_preparation`), G8 V-G8TL-2 (`set_feature_flag`), the composition_pg legacy_usd price seed, M6 WR-7/WR-8, and L3-REBASE F2 |
| Branch / worktree | `codex/d10-followup` / `.claude/worktrees/codex-d10-followup` |
| Base / code head | `19670a8c` (claude/consumer-v1) / `4b462094` |
| Commits | `e911d989` 0022 + adapter + checks; `b4a335fe` mutants, unit case, upgrade path, runtime list; `ba45d386` composition_pg seed; `5412c8fb` WR-7/WR-8; `aabe07f7` wiring patch; `3c1746db` register mutants moved to 0022, adapter case leaves the role NOLOGIN; `9c0e215f` F2 revoke; `4b462094` layer-3 hunks; then this evidence commit |
| Isolation | `INFRX_D_TASK=d10`: `infrx-d10-postgres` / `-supabase` on 55442, `infrx-d10-valkey` on 55469. No hosted DB, pilot box, AWS or SSM. The harness removed its containers at the end. Scratch clones of `codex/m6-phase2` and `codex/l3-rebase` live under the session scratchpad. |
| Migrations | `git diff 19670a8c -- 0001..0021` is empty. `0022_preparation_refusal_and_flag_writer.sql` is new: 271 lines, sha256 `a54eca595e40eb4f176dd45564f705501a080c5867a9393e686b9b8de8c4f4ee`. The door revoke is not included (see §5). |

## 1. Items

| Item | Status | What |
|---|---|---|
| (1) `fail_preparation(lease, cause)` | done | `infrx.fail_preparation(jsonb)` takes `{lease, cause, limits}`. The cause must be `invalid_media` or `preparation_failed`; anything else is `invalid_request` and nothing is read. The replay of the same lease's identical call answers the committed outcome. The key is `jobs.proposal` = `{cause, usage:null, result_ref:null, fail_preparation:{generation, worker_id}}`, written before the end and immutable after it (0018). Then 0016 `fence_lease(['preparation'])` runs: kind, terminal, live attempt, generation, owner, R29 deadline (terminalized and returned as data, R39), expiry. Then 0016 `terminalize_no_usage(..., 'failed')` sets released_free and releases the hold in its own unit, every reservation (preparation, inference and journal) and the attempt, and writes one usage and one trace projection; 0017's trigger writes the terminal event. Adapter: `PgJobStore.fail_preparation(lease, cause) -> TerminalOutcome` in `infrx/state/jobstore.py`, via `_fenced`, so refusals are typed. This is the call W5's `PreparationRunner._end` makes (`getattr(self.jobs, "fail_preparation")`). Grants: service_role and infrx_runtime. |
| (2) `set_feature_flag` (V-G8TL-2 option A) | done | Signature `(text, boolean, text, text) returns boolean`, SECURITY DEFINER, `lock table infrx.feature_flags in exclusive mode`, then the guarded, attributed UPDATE (`left(actor,200)`, `left(reason,500)`), returning `found`. Revoked from public, anon and authenticated; granted to service_role only. G8's call site is wiring request W1. |
| composition_pg legacy_usd | done | Fixed in the test world, not in the probe. The `legacy_usd` case first asserts `RuntimeMisconfigured(price_source)` without a USD row, which is the probe refusing correctly. It then seeds the two hosted rows (`checks_reads.seed_hosted_usd`, `effective_from = infrx.now()` on the frozen clock) and composes. |
| M6 WR-7 | done (SQL); fake is W2 | 0022 does `create or replace` of 0019's `register_content` and `content_objects_guard`, one change each. When a `written` registration finds a LIVE row with the same digest, it sets `eligible_at = greatest(eligible_at, now + grace)` under the row lock. A `discovered` registration never does this. The guard now lets a live row's eligibility move later, never earlier, and never on a retiring row. A claim granted before the refresh can no longer tombstone (`not_claimable`/`not_eligible`, 0020 recheck). Grants are kept (create or replace). |
| M6 WR-8 | retired in 0022's header | 0019's claim "not before the claim that deleted it has lapsed" was never implemented and will not be. `deleted` comes only from `content_acknowledge_delete`, which runs after the store confirms the delete. The delete is issued only with at least one request timeout left on the claim. Generation n>1 lives at its own name (`retention.generation_key`), so a late delete reaches only its own generation. |
| L3-REBASE F2 | done | `revoke all on function infrx.jobs_result_expiry_guard() from public, anon, authenticated, service_role`, as for its siblings. |
| Door revoke (`admit` / `claim_preparation` from infrx_runtime) | not done, by the brief | This lane must not do it. It belongs in 0023, after W5 and G7 merge. |

## 2. Privileges changed by 0022

| Object | Role | Before | After | 0022 line |
|---|---|---|---|---|
| `infrx.fail_preparation(jsonb)` (new) | service_role | — | EXECUTE | 105-106 |
| same | infrx_runtime | — | EXECUTE | 106 |
| same | public / anon / authenticated | — | none | 105 |
| `infrx.set_feature_flag(text,boolean,text,text)` (new) | service_role | — | EXECUTE | 122-124 |
| same | public / anon / authenticated / infrx_runtime | — | none | 122-123 |
| `infrx.jobs_result_expiry_guard()` | service_role | EXECUTE (0004:53 default) | none | 270-271 |
| `infrx.register_content(jsonb,double precision)`, `infrx.content_objects_guard()` | all | nobody (0019) | unchanged (create or replace keeps the ACL) | — |

`D10-runtime-functions.txt` gains `infrx.fail_preparation(jsonb)`. The layer-3 map and migration pin are wiring request W4.

## 3. Commands (all run by me; `cd apps/infrx-api`, `INFRX_D_TASK=d10 INFRX_D2_VALKEY_CONTAINER=infrx-d10-valkey INFRX_D2_VALKEY_PORT=55469`)

| Command | Head | Exit | Result |
|---|---|---|---|
| fails-before: `pytest tests/d/test_followup_d10.py` with 0022 moved aside | `e911d989` | 1 | 5 failed (the functions do not exist) |
| fails-before, V-G8TL-1 shape: mutants `d10_flag_writer_row_lock_only`, `_share_row_exclusive`, `_starves_under_load` (0006's row UPDATE, or a lock that does not conflict with ROW SHARE) | `b4a335fe` | 0 | killed by the queue and overlapping-lockers checks |
| fails-before, composition_pg[legacy_usd] | `b4a335fe` | 1 | `RuntimeMisconfigured: INFRX_MODE='pilot': unreachable at startup: price_source`; passes at `ba45d386` (2 passed) |
| fails-before, WR-7: M6 `test_a_source_fetched_again_is_not_collected_before_its_admission` (xfail removed), scratch merge of `codex/m6-phase2` `ff5aedb1` + this branch, refresh dropped from both 0022 and the fake | `5412c8fb` | 1 | [f2c] and [d10] both failed (`not_found` at admission) |
| same, with 0022 + W2's fake patch | `4b462094` | 0 | **[f2c] PASSED, [d10] PASSED**; whole `tests/m/test_retention.py` + `tests/d/test_lifecycle_conformance.py` = 102 passed |
| `pytest -q tests/d` (default subset) | `4b462094` | 1 | **841 passed, 1 failed, 1 skipped, 8 xfailed**. The one failure is `test_lifecycle_conformance::test_the_versioned_acceptance_transcripts_replay_exactly`, expected until W2 lands (§4) |
| `INFRX_D1_IMAGE=supabase pytest -q` on test_ready, test_content, test_reads, test_lifecycle_conformance, test_upgrade_d10, test_postgrest_d10, test_catalog_pg, test_followup_d10 and test_composition_pg | `4b462094` | 1 | **133 passed, 1 failed** (the same transcript case), 4 xfailed |
| `pytest tests/d/test_lifecycle_conformance.py` with W2's patch applied temporarily, plain / Supabase | `4b462094` | 0 / 0 | **27 passed / 27 passed** (patch then reverted; tree clean) |
| `INFRX_MUTANTS=all pytest -q tests/d/test_migration_mutants.py tests/d/test_code_mutants*.py` | `4b462094` | 0 | **732 passed, 0 survivors** (22 new migration mutants + 2 code mutants; the 4 `register_content` mutants re-anchored from 0019 to 0022) |
| `pytest -q tests/contracts -k 'not pg'` | `4b462094` | 0 | **1260 passed**, 6 deselected |
| `python3 research/plan/scripts/validate_plan.py` | `4b462094` | 0 | PASS (3 lines) |
| I8 `infra/runbooks/privilege_probe.py --role infrx_runtime --allow-functions research/plan/evidence/d/D10-runtime-functions.txt` against a d10 stand-in (0001-0022, random password never printed, role back to NOLOGIN after), plain / Supabase | `4b462094` | 0 / 0 | **PASS, 64 checks, 0 failed** on each, incl. `executes infrx.fail_preparation(jsonb)` |
| layer-3: scratch merge `codex/l3-rebase` `02eb87ff` + this branch + W4, `tests/integration/test_harness.py` | `9c0e215f` | 0 | 27 passed |
| layer-3 map vs a d10 0001-0022 catalog (scratch script: every SECURITY DEFINER function x anon/authenticated/service_role, and the runtime surface) | `9c0e215f` | — | Without W4, the four 0022 deltas show (2 missing entries, guard service_role, runtime +fail_preparation). With W4 they are gone. The script's naming noise for `public`/pgcrypto is the same both ways. |
| G8 `tests/g/ops/test_transition_pg.py` on the scratch merge with W1 | `5412c8fb` | 0 | 6 passed |

New tests: `tests/d/test_followup_d10.py` has 6 real-PG cases: fail_preparation in both regimes (refusals, conservation, replay, lapsed, queued, inference lease, deadline, cancel-first), grants, the WR-7 refetch, the writer queues a new FOR SHARE reader behind it (deterministic), eight overlapping 200 ms lockers (the write lands, `changed=True`, in 0.12 s against a 2 s bound), and the adapter as the `infrx_runtime` login. `test_settle_units.py` has one adapter unit case. Mutants: 16 for fail_preparation/flag writer, 5 for WR-7, 1 for F2, and 2 code mutants. Each is killed by an assertion of its named check. Two "revoke removed" mutants were equivalent (0004's default privileges already withhold them) and were replaced by explicit browser grants.

## 4. Wiring requests (exact hunks in `D10-followup-wiring.patch`; not applied)

- **W1, G8**: `infrx/operations/transition.py` `PgTransition.set_flag`. Replace the UPDATE with `(changed,) = await (await conn.execute("select infrx.set_feature_flag(%s, %s, %s, %s)", (name, enabled, actor, reason))).fetchone()` and `return changed`. The SET LOCAL bounds and the 55P03/57014 mapping stay. Proof: `tests/g/ops/test_transition_pg.py` 6 passed on the scratch merge. G8 then tightens its overlapping case to `changed is True`, which D10's `check_flag_writer_lands_under_overlapping_lockers` already shows.
- **W2, F2C (contracts; must merge WITH this branch)**:
  - `infrx/contracts/fakes/lifecycle.py` `_register` gets the same WR-7 rule.
  - `fixtures/acceptance/lifecycle.json` is regenerated (`python -m infrx.contracts.conformance.acceptance --write`). Two `eligible_at` values move (t+150 to t+210, t+60 to t+120).
  - Without W2, `tests/d/test_lifecycle_conformance` fails on this branch. With it, 27/27 pass on both images, and contracts show 1260 passed on the scratch merge.
- **W3, M6**: `tests/m/test_retention.py`: remove the strict xfail on `test_a_source_fetched_again_is_not_collected_before_its_admission`. It passes on [f2c] and [d10] with 0022 + W2.
- **W4, layer 3** (`tests/integration/pgstate.py`, `test_harness.py` on `codex/l3-rebase`):
  - `FUNCTIONS` adds `infrx.fail_preparation(jsonb): SERVICE` and `infrx.set_feature_flag(text,boolean,text,text): SERVICE`.
  - `infrx.jobs_result_expiry_guard()` goes from SERVICE to NOBODY.
  - `RUNTIME_FUNCTIONS` adds `infrx.fail_preparation(jsonb)`.
  - The migration pin adds `0022_preparation_refusal_and_flag_writer.sql`.
- **W5, W5 lane**: no code change. `PgJobStore` now has `fail_preparation`, so `_end` takes it instead of the lapse fallback. The F2C port gets `JobStore.fail_preparation(lease, cause) -> TerminalOutcome` when F2C freezes it; the W5 draft `FailPreparation` fake has no replay, while PG replays the same lease's identical call.
- **W6, I8 (optional)**: add `("flip a flag through the operator writer", "select infrx.set_feature_flag('signup_grant', false, 'x', 'y')")` to `privilege_probe.MUST_DENY`. D10's own check already asserts that infrx_runtime is denied.

## 5. Open issues

- The door revoke (`infrx.admit`, `infrx.claim_preparation` from infrx_runtime) is not in 0022, per the brief. It goes in 0023 after W5 + G7 merge, with the RUNTIME_FUNCTIONS and runtime-list updates.
- The transcript failure on the branch alone is the W2 merge-order dependency above.

## 6. Proposed ruling text

- **fail_preparation.** A preparation worker ends a job itself only for a permanent refusal (unsupported_media and request_too_large map to invalid_media; context_length_exceeded maps to preparation_failed), through the store's fenced `fail_preparation(lease, cause)`. It is fenced like `prepared`, released free, and settled once. Only the same lease's identical call replays; every other refusal lapses its lease.
- **Flag writes.** The operator writes a regime flag only through `infrx.set_feature_flag` (EXCLUSIVE table lock, then the guarded update), bounded by the caller's lock/statement timeouts. New admissions queue behind a waiting freeze.
- **Written re-registration.** A `written` registration of the same bytes at a live key restarts its grace (never shortens it); a `discovered` one never does.

## 7. Remaining effort

Optimistic 0.5 h, likely 1.5 h, pessimistic 4 h; confidence medium. Basis: every D list is green except the W2-gated transcript. What remains is the coordinator merge with W1-W4 and the 0023 door revoke after W5/G7 (about 1 h).

## Verification log

- 2026-09-25T19:10Z: written at code head `4b462094`. Chain log is in the session scratchpad (`chain.log`, `probe-*.log`).
