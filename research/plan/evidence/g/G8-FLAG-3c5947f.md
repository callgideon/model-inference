# G8-FLAG — the audited `flag` verb (G8 operations; GAP-I3-1, I3R-7, R144)

- Base `34f0ed28`; head `3c5947f` (code + doc edits) on `codex/g8-flag`; this evidence commit follows.
- Changed: `apps/infrx-api/infrx/operations/{cli,transition,service}.py` (additive),
  `apps/infrx-api/tests/g/ops/{test_flag.py,test_flag_pg.py}` (new), `tests/g/ops/mutants.py`
  (+6 mutants), `tests/g/ops/test_mutants.py` (subset +1), `infra/runbooks/rollback.md`
  (#maintenance: one sentence + log), `infra/app/README.md` (`signup_grant` row + log).
  No migration, no SQL object: the writer is 0022's `infrx.set_feature_flag`, through the
  existing `PgTransition.set_flag` (same `SET LOCAL lock_timeout/statement_timeout`, same
  55P03/57014 → `FlagLocked` mapping). Nothing hosted, no box, no AWS; docker g8 only (55447).

## The verb's contract
`python -m infrx.operations.cli flag --name <flag> (--on | --off) --reason <text> --idempotency-key <key> [--dry-run] [--lock-timeout-s 5.0]`

| input | result |
|---|---|
| a non-regime flag in `infrx.feature_flags` (e.g. `signup_grant`) | one JSON line `{name, enabled_before, enabled_after, changed, replayed, actor, updated_at}`, exit 0; written by `transition.set_flag` → `PgTransition.set_flag` → `select infrx.set_feature_flag(name, enabled, actor, reason)` |
| same key, same payload | the recorded result, `replayed: true`, no second write, no second audit row |
| same key, other payload | `idempotency_conflict`, exit 1, nothing written |
| already in the requested state | `changed: false`, still audited once under its key |
| `credit_admission` / `legacy_usd_admission` (`transition.REGIME_FLAGS`) | `invalid_request` naming `credit-transition` (R133/R144), exit 1, nothing written or audited |
| unknown name | `not_found`, exit 1, nothing written or audited |
| a flag table another session holds (any admission's `require_feature … FOR SHARE`) past `--lock-timeout-s` | `state_conflict` "… stayed locked past … s …; nothing changed - rerun under the same key", exit 1, nothing audited |
| `--dry-run` | the current row `{name, enabled, updated_by, reason, updated_at}`, exit 0, no operator key needed, no write (regime/unknown refused as above) |

- Audit: `_once("flag", …)`, action `admin_set_entitlements` (0009's closed set; no new
  action), `target_org_id` null, `before = {enabled}`, `after.request = {name, enabled}`.
- Actor: the operator session's `principal` (the operator key id) on the row
  (`updated_by`) and the audit row (`actor_principal`); the CLI has no actor argument
  (`--actor` is an argparse error). Money: never read or written (footprint unchanged).

## Fails before / passes after
| cmd (in apps/infrx-api, `INFRX_D_TASK=g8`) | exit | result |
|---|---|---|
| `pytest -q tests/g/ops/test_flag.py tests/g/ops/test_flag_pg.py` before the code (tests only) | 1 | 6 failed, 1 passed — every verb case fails (`invalid choice: 'flag'`); the passing one is `test_flag__the_writer_is_set_feature_flag_under_the_bounded_lock`, which pins the reused writer (it already existed) |
| same, head | 0 | 7 passed |

| case | proves |
|---|---|
| `test_flag__signup_grant_goes_off_and_on_through_the_audited_writer_as_the_operator` | off/off/on, audit per key, replay without a write, conflict, actor = principal, no `--actor` |
| `test_flag__a_regime_flag_an_unknown_flag_and_a_locked_flag_change_nothing` | regime refused naming credit-transition, not_found, FlagLocked → state_conflict |
| `test_flag__the_dry_run_reads_without_a_key_and_writes_nothing` | dry run: row, no key, no write; regime refused; key/reason required otherwise |
| `test_flag__the_writer_is_set_feature_flag_under_the_bounded_lock` | the only write is `set_feature_flag` after the two SET LOCALs |
| `test_flag_pg__signup_grant_off_off_on_is_audited_once_per_key_and_read_at_once` | PG: attribution on the row, `require_feature('signup_grant')` 55000 right after `--off` and admitted right after `--on`, one audit row per key, replay of k1 after k3 writes nothing, conflict, money tables unchanged |
| `test_flag_pg__regime_unknown_and_dry_run_write_nothing` | PG: whole footprint (flags, audit, money, jobs, keys, cards) unchanged |
| `test_flag_pg__an_admission_holding_a_flag_bounds_the_write` | PG: a session holding `require_feature('credit_admission')` (FOR SHARE) keeps `signup_grant`'s write waiting ≤ 0.5 s + margin → exit 1, unchanged, not audited; after it ends the same key succeeds |

## Mutants (G ops list; all killed)
| mutant | edit | fake kill (runner) | PG kill (applied to the worktree, `test_flag_pg.py`) |
|---|---|---|---|
| `flag_regime_refusal_dropped` | `if name in REGIME_FLAGS:` → `if False:` | killed (2 cases) | killed (`regime_unknown_and_dry_run`) |
| `flag_actor_from_argument` | actor `op.principal` → `reason` | killed | killed (`off_off_on`) |
| `flag_audit_skipped` | `_once(...)` → `(await write(""))[1], False` | killed | killed (`off_off_on`) |
| `flag_direct_update` | `select infrx.set_feature_flag(…)` → a direct attributed `UPDATE infrx.feature_flags` (CTE) | killed (`writer_is_set_feature_flag`) | killed (`lock` case: the UPDATE runs past the FOR SHARE on another flag; the other two PG cases pass under it, so the kill is the lock, not broken SQL) |
| `flag_off_ignored` | `enabled=a.enabled` → `enabled=True` | killed | killed (`off_off_on`, `lock`) |
| `flag_lock_unmapped` | `except FlagLocked:` → `except ZeroDivisionError:` (declared `dies_by=FlagLocked`) | killed | killed (`lock`) |

The G ops runner excludes `test_*_pg.py` (it strips service env and contends for the port);
the PG column is the recorded oracle run
(`scratchpad …/g8flag/pgkill.py`: apply one mutant to the worktree file, run `test_flag_pg.py`, restore; `git status` clean afterwards).

## Commands (all `INFRX_D_TASK=g8`, in `apps/infrx-api` unless noted)
| cmd | exit | result |
|---|---|---|
| `make api-env` (worktree root) | 0 | pinned env |
| `uv run --frozen --no-sync python -m tests.g.ops.mutants flag_regime_refusal_dropped flag_actor_from_argument flag_audit_skipped flag_direct_update flag_off_ignored flag_lock_unmapped` | 0 | 6/6 killed (actor anchor first misdeclared - two occurrences - then widened; killed) |
| `uv run --frozen --no-sync pytest -q tests/g/ops tests/g/test_mutants.py -k "ops or flag or transition"` | 0 | 95 passed, 32 deselected |
| `uv run --frozen --no-sync pytest -q tests/g` | 0 | 716 passed, 4 skipped |
| `INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/g/ops/test_mutants.py` | 0 | 108 passed (102 mutants killed, list well-formed, every case covered, runner self-tests) |
| `uv run --frozen --no-sync pytest -q ../../tests/integration/backend/recovery/test_runbooks.py ../../tests/integration/ops/test_i3_operations.py` | 0 | 18 passed (rb03: the maintenance statement is still bk04's verbatim) |
| `uv run --frozen --no-sync ruff check infrx/operations tests/g/ops` | 0 | All checks passed |
| `python3 research/plan/scripts/validate_plan.py` (worktree root) | 0 | PASS (3 lines) |
| `git diff --stat 34f0ed28..3c5947f` | 0 | 9 files, owned paths only |

## I3R-7 / GAP-I3-1 doc edits
- `infra/runbooks/rollback.md#maintenance`: one sentence — the statement covers only the two
  admission flags; `signup_grant` is written only with the audited verb (R144). The SQL
  block is unchanged (rb03 pins it to bk04).
- `infra/app/README.md` §4 table: the `signup_grant` row names the verb (`--on`/`--off`).
- **Not edited (not an owned path):** the cutover rollback's own step lives in
  `infra/app/operations.md` "Cutover rollback" step 2, which still says no verb exists and
  sends the operator to the logged flag write → WR-G8FLAG-1.

## Wiring requests
- **WR-G8FLAG-1** (`infra/app/operations.md`, "Cutover rollback" step 2), replace the step's text with:
  "**Signup grant flag off** [OP]: `python -m infrx.operations.cli flag --name signup_grant --off --idempotency-key <k> --reason "<why>"` (audited once per key, R144; exit 1 `state_conflict` when an admission holds the flags past `--lock-timeout-s` — rerun under the same key) — a flag, never money. A claim then answers `unavailable` and `/welcome` offers a retry (`app/(auth)/flow.ts` `claimOutcome`)."
- **WR-G8FLAG-2** (`research/plan/08-contracts-v1-encoding.md` R144, one clause): "… The App's cutover rollback (I3) uses this audited writer through the operator verb `flag` (G8-FLAG), which refuses the two regime flags, never a direct flag write."
- **WR-G8FLAG-3** (note, coordinator decision): `rollback.md#maintenance`'s statement is a direct
  `UPDATE` of the two regime flags (pinned verbatim by bk04/rb03), which R144's first sentence
  ("the operator writes a regime flag only through `infrx.set_feature_flag`") now contradicts.
  Either amend R144 to except the maintenance drill, or move maintenance to
  `select infrx.set_feature_flag(...)` in bk04 + rb03 + the runbook together (D10/I3B owners).
- Close GAP-I3-1 in `research/plan/evidence/i/I3-prep-bf29b92.md`'s successor / the manifest I3 row.

## Deviations
- `--lock-timeout-s` (default 5.0) added so the bound is the operator's and the PG lock case is fast; `--dry-run` needs no operator key (mirrors `credit-transition --dry-run`).
- The verb reads rows through `PgTransition.inventory()["flags"]` (existing read-only snapshot) rather than a new query.

## Fix round (review of handback 086f99d5; code head unchanged 3c5947fb, doc head 44d623c8)
All three findings fix text outside the lane's owned paths or need a coordinator decision. Nothing
in `infrx/operations` changed; the only commit is to `infra/runbooks/rollback.md` (owned).

| finding | fixed | what |
|---|---|---|
| 0-G8FLAG-R1, 1-G8FLAG-R1 (operations.md cutover rollback step 2 still sends the operator to a direct `signup_grant` write) | no (not owned) | WR-G8FLAG-1 is now an exact two-file patch with its own test (below), fails-before and passes-after verified in this worktree and reverted; the coordinator lands it in the same merge as `codex/g8-flag` and closes GAP-I3-1 |
| 1-G8FLAG-R2 (maintenance is a direct UPDATE of the two regime flags vs R144's first sentence) | no (coordinator decision, WR-G8FLAG-3) | the owned sentence no longer reads as the admission flags' path (they move with `credit-transition`, R133), and a `⚠️ TO BE VERIFIED (WR-G8FLAG-3)` note under the block states the conflict and the two options; the SQL block is unchanged (bk04/rb03) |

### WR-G8FLAG-1, exact patch (replaces the text-only request above)
`infra/app/operations.md` "Cutover rollback" step 2, and one assertion in
`tests/integration/ops/test_i3_operations.py::test_i3_ops03_every_cutover_step_names_its_check_and_abort`:
```diff
-2. **Signup grant flag off** [OP]: no CLI verb turns `signup_grant` off on its own
-   (`credit-transition` only enables it; GAP-I3-1 asks G8's owner for one). Until then it is
-   the logged flag write of [rollback.md](../runbooks/rollback.md#maintenance) with
-   `where name = 'signup_grant'`, your name and reason — a flag, never money. A claim then
-   answers `unavailable` and `/welcome` offers a retry (`app/(auth)/flow.ts` `claimOutcome`).
+2. **Signup grant flag off** [OP]:
+   `python -m infrx.operations.cli flag --name signup_grant --off --idempotency-key <k> --reason "<why>"`
+   (`infrx.set_feature_flag`, audited once per key, R144; exit 1 `state_conflict` when an
+   admission holds the flags past `--lock-timeout-s` — rerun under the same key) — a flag,
+   never money. A claim then answers `unavailable` and `/welcome` offers a retry
+   (`app/(auth)/flow.ts` `claimOutcome`).
```
```diff
     assert "Not rolled back" in rollback and "money" in rollback and "users" in rollback
+    # R144 / G8-FLAG (review 0/1-G8FLAG-R1): the grant closes with the audited verb, never
+    # the maintenance block's direct flag write.
+    assert "flag --name signup_grant --off" in rollback, "cutover rollback step 2 skips the verb"
+    assert "where name = 'signup_grant'" not in rollback and "no CLI verb" not in rollback
```
Plus one line in operations.md's verification log (coordinator's wording). WR-G8FLAG-2 (the R144
clause naming the `flag` verb) stands unchanged.

### Commands (fix round; `INFRX_D_TASK=g8`, in `apps/infrx-api` unless noted)
| cmd | exit | result |
|---|---|---|
| test hunk of WR-G8FLAG-1 applied alone; `pytest -q ../../tests/integration/ops/test_i3_operations.py -k ops03` | 1 | 1 failed (`cutover rollback step 2 skips the verb`) - fails-before |
| both hunks applied; `pytest -q ../../tests/integration/ops/test_i3_operations.py ../../tests/integration/backend/recovery/test_runbooks.py` | 0 | 18 passed; reverted (`git apply -R`, `git status` clean) |
| `git apply --check` of the patch at 44d623c8, then applied: the same two files | 0 | 18 passed; reverted, clean |
| at 44d623c8: `pytest -q ../../tests/integration/backend/recovery/test_runbooks.py ../../tests/integration/ops/test_i3_operations.py` | 0 | 18 passed (rb03: the statement still bk04's verbatim) |
| `pytest -q tests/g/ops` | 0 | 94 passed (PG cases on the g8 harness) |
| `python -m tests.g.ops.mutants <6 flag mutants>` | 0 | 6/6 killed |
| `INFRX_MUTANTS=all pytest -q tests/g/ops/test_mutants.py` | 0 | 108 passed (102 mutants killed, list well-formed, every case covered, runner self-tests) |
| `ruff check infrx/operations tests/g/ops` | 0 | All checks passed |
| `python3 research/plan/scripts/validate_plan.py` (worktree root) | 0 | PASS (3 lines) |

Estimate (unchanged work, coordinator side): optimistic 0.2 h, likely 0.4 h, pessimistic 1.0 h,
confidence medium; basis: apply WR-G8FLAG-1 (verified patch) and WR-G8FLAG-2 at the merge, decide
WR-G8FLAG-3 (R144 exception, or bk04 + rb03 + runbook to `set_feature_flag` by D10/I3B).
