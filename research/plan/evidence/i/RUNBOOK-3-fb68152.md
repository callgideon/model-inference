# RUNBOOK-3 — the W7f reversal drilled, and the reinstall rule (E4C window prerequisites)

Base `9b21339a` (claude/consumer-v1) · implementation head `fb681526` (commits `8d84a516` and
`fb681526`) · branch `codex/runbook-3` · worktree `.claude/worktrees/codex-runbook-3`. This is a
support lane, so the manifest gates are unchanged. It changed no product code under
`apps/infrx-api/infrx/` and touched nothing on the box, AWS, SSM or hosted. Docker was
task-local only: `INFRX_D_TASK=revoke` (postgres 55459).

It closes the two open issues from `research/plan/evidence/e/E4C-runbook-2-e9b2128.md` §"Open
issues after the round":

- "The reversal path, W7f and then `--to legacy_usd` with a legacy release restored, has never
  been drilled." The database half is now drilled. The box half is still an operator step, and
  the runbooks say so.
- "The worker's `MONITOR_DATABASE_URL` … is still dropped by a reinstall until step 55 reruns."
  This lane closed it with option (b): an explicit, tested runbook rule. The reasons are below.

## Changed paths (owned only)

| Path | Change |
|---|---|
| `apps/infrx-api/tests/g/ops/test_reversal_pg.py` (new) | The PostgreSQL drill (item 1). |
| `apps/infrx-api/tests/g/ops/test_reversal.py` (new) | The same reversal at the seam the mutants edit: a scripted store, runner-visible. |
| `apps/infrx-api/tests/g/ops/test_transition.py` | `ScriptedStore.regime` (default `legacy_usd`), so a scripted store can hold CREDIT work in flight. Existing cases are unchanged. |
| `apps/infrx-api/tests/g/ops/mutants.py` | Three mutants: `reversal_skips_the_drain`, `reversal_audits_as_forward`, `replay_reruns_the_freeze`. |
| `infra/runbooks/rollout.md` | §3 states the reversal's keys and its proof, and adds **The reinstall rule**. The W7f row's rollback cell points to §3. The Known-good paragraph now reads: database half drilled, box half not. A log entry is added. |
| `infra/runbooks/rollback.md` | Rollout rollback step 4 runs the reversal first. The drill gets a new step 3b (the reversal). Step 4 installs the target without the regime names and runs 55 only for an R127 release. Step 6 re-activates CREDIT under a new key and runs 55. A log entry is added. |
| `infra/rollout/steps/55-runtime-login.sh` | Header comment only: never run after a pre-R127 target. |
| `tests/integration/ops/test_runbook_reversal.py` (new) | Runbook-structure tests rv01 and rv02. |

`infra/rollout/README.md`, `50-install.sh` and `apps/infrx-api/deploy/install.sh` are
unchanged: no step changed behaviour.

## Item 1 — the reversal drill

The drill is
`tests/g/ops/test_reversal_pg.py::test_reversal_pg__credit_back_to_legacy_usd_drains_keeps_credit_exact_and_replays_nothing`.
It runs on the pilot-shaped PG world of `test_transition_pg.pilot`, with every step through
`cli.main`:

1. Publish the FIXTURE card, then run K1 `credit-transition --card …`, which exits 0 (the W7f
   path).
2. Admit one CREDIT job and settle it in CREDIT. It charges exactly `0.00111000` at the card:
   1,200 in and 340 out at 0.5/1.5 per million. Admit a second CREDIT job and leave it in
   flight.
3. The reversal's `--dry-run` leaves `footprint` unchanged (every money, job, card, key, audit
   and flag table). It exits 1 with `in_flight`, `would_change` = [freeze credit, enable
   legacy_usd], and `restart_with` `{ACCOUNTING_REGIME: legacy_usd}`.
4. K2 with `--drain-timeout-s 0.3` goes past the bound: exit 1, `state_conflict`, `in_flight`.
   `applied` = [credit_admission off] only. The flags are credit f, legacy f, signup t. There
   is no K2 audit row. Legacy admission is refused (not until drained), and CREDIT admission is
   refused (frozen).
5. The in-flight job settles **in CREDIT** at the card. The K2 rerun under the same key exits 0
   with `applied` = [legacy_usd_admission on]. After it, CREDIT admission is refused and
   legacy admission is accepted.
6. Every CREDIT row (ledger, holds, wallets) is value-equal to the rows before the rerun, even
   after a new USD admission. The two `inference_debit` rows are exactly `-0.00111000` each, and
   `drift == []`.
7. Audit: K1 and K2 each have one row. The actor is the operator key id, the action is
   `admin_set_entitlements` and the operation is `transition`. K1 records before = {legacy t,
   credit f, signup f}, request target `credit` with the FIXTURE card, result flags {credit t,
   legacy f, signup t} and `restart_with` naming the card. K2 records before = {legacy f,
   credit f, signup t} (the stopped run's freeze), request target `legacy_usd` with card
   `None`, and its result equals the CLI output.
8. The K2 replay prints the identical output with `footprint` unchanged. The K1 replay after
   the reversal also prints K1's recorded output and writes nothing, and CREDIT stays off. This
   is why a roll-forward needs a **new** key.

**The order proof.** The run stopped at the bound shows the freeze without the enable. The
rerun then enables only after the job has drained. The fake-level case pins the call order
within a single run: `set credit off` < `inventory 0 in flight` < `set legacy on`.

### Mutants (tests/g/ops/mutants.py, case `test_reversal__credit_is_frozen_drained_then_legacy_enabled_audited_once_as_the_reversal`)

| Mutant | Edit | Runner | Also on the PG drill (manual, original bytes restored) |
|---|---|---|---|
| `reversal_skips_the_drain` | `transition.py`: `while target == CREDIT and in_flight(...)` | killed (the enable comes before `inventory 0`) | killed: the stopped run's `applied` also enables legacy |
| `reversal_audits_as_forward` | `transition.py`: the audited request's `"target": CREDIT` | killed | killed: `('credit', None) == ('legacy_usd', None)` |
| `replay_reruns_the_freeze` | `service.py` `_once`: `if prior is not None and operation != "transition":` | killed (the replay touched the store) | killed: "K1's replay re-activated" (exit 1: it froze legacy_usd again with a USD job in flight) |

The PG runs are not in the runner (`--ignore-glob=test_*_pg.py`, as for every G8 PG suite). Their
oracles are the manual runs above. `git status` afterwards showed no change under `infrx/`.

### Finding (product, not fixed: `infrx/` is not owned)

**F-1: the transition verb's replay does not say it is a replay.** The brief asks for "a replay
of K2 answers replayed:true". `transition.apply` discards `_once`'s `replayed` flag
(`result, _ = await op._once("transition", …)`, `infrx/operations/transition.py`), so the CLI
prints the recorded result unchanged. The `flag` verb, by contrast, returns
`{**result, "replayed": replayed}`. The drill therefore asserts that the replay's output
equals the first run's and that it writes nothing (`footprint`), which is the safety property.
The operator still cannot tell a replay from a fresh run by its output: a K1 replay after a
reversal "succeeds" and leaves CREDIT off. Wiring request 1 carries the patch. The runbook
states the current behaviour ("no `replayed` field on this verb").

## Item 2 — the monitor DSN across a reinstall: option (b), with reasons

**How a 50-install rewrites the env file.** `50-install.sh` runs `install.sh`. Its step 4 runs
`preflight.py apply`, whose `collect()` builds the **whole** file from the MANIFEST: SSM leaves
(`DATABASE_URL` ← `pg_journal_url`, `MONITOR_DATABASE_URL` ← `monitor_database_url` only if that
leaf exists), the installer's local values and `--set` tunables. `stage()` and `commit()` then
replace the file with one rename. No line of the previous file is carried over.
`DATABASE_URL` and `MONITOR_DATABASE_URL` are not TUNABLE, so `--set` cannot carry them either.

**Why not (a).** A preserve outside `preflight.py` is not ≤ 20 lines, and a blind preserve
would also be wrong:

1. install.sh has restarted the units and waited for `/readyz` before control returns. Putting
   the two lines back afterwards means another staged write, envcheck, restart and readiness
   wait, which is 55's body again.
2. A preserve would carry `infrx_runtime` into the install of a **pre-R127 known-good target**
   (4226315, bda1586). Their pool runs `set role service_role` on every connection (`git show
   4226315:apps/infrx-api/infrx/gateway/pilot.py:133`), and 0021 makes `infrx_runtime` "a member
   of no role" (`0021_read_authority.sql:461`), so every connection would fail. Which DSN is
   correct depends on the release, and only the runbook step knows that.

**(b), as landed.** The reinstall rule is in rollout.md §3, and 55's header states the
exception:

- Every 50-install of a release with R127's dedicated logins is followed by
  `55-runtime-login.sh`. That covers W10 → W10b, already adjacent, and the drill's roll-forward.
- Never after a pre-R127 target: it stays on `pg_journal_url`.
- Env-file restores (`rollback.sh` via 90-revert/R2, 55's own exit 4, R4's snapshot) need no
  rerun.

Test rv02 checks every rollout.md window row whose step is `50-install.sh` is followed by a row
whose step is `55-runtime-login.sh`. It checks §3 states the rule with both pre-R127 targets and
"never". In rollback.md's drill, every step with a 50-install, and the roll-forward, names 55
after the install.

**Still open.** A reinstall still drops the worker's monitor DSN until 55 runs. The rule makes
the rerun explicit but does not make the DSN durable. The durable fix is either the SSM leaf
`/model-inference/monitor_database_url`, which preflight already reads, or a preflight leaf for
the runtime login. Both are coordinator/preflight items outside this lane.

## Runbook rows (before → after, abridged)

- **rollout.md §3, after the W7f-reversal paragraph.** Before: nothing on keys, proof or
  reinstalls. After: "Its keys: one per reversal (`revert-<window id>`). A run stopped at the
  drain bound … rerun it under the **same** key … A finished key's replay prints the recorded
  result (no `replayed` field on this verb) and writes nothing, so W7f's own key never
  re-activates CREDIT: a roll-forward reruns W7f's `credit-transition --card …` under a
  **new** key. `--dry-run` writes nothing … Proof on PostgreSQL: `…test_reversal_pg.py::…`,
  with the mutants … The box half … stays an operator step that no test runs." It is followed
  by the new paragraph "**The reinstall rule.** …".
- **rollout.md W7f rollback cell.** "`credit-transition --to legacy_usd` (PI P-02)" →
  "`credit-transition --to legacy_usd` (PI P-02; §3: its keys and its drill)".
- **rollout.md Known-good record.** "… (R2 restores it), a path no drill has run." → "… (R2
  restores it). The database half of that path is drilled (`test_reversal_pg.py`, above); the
  box half is not."
- **rollback.md Rollout rollback step 4.** Before: `rollback.sh "$BACKUP"` with no regime note.
  After: "After W7f …, when the backup's env file runs `legacy_usd` …, first the W7f reversal
  from the coordinator host: `credit-transition --to legacy_usd` (rollout.md §3 …)".
- **rollback.md drill 3b (new).** "**Reverse W7f** when hosted admits CREDIT only and the target
  runs `legacy_usd` …: `python -m infrx.operations.cli credit-transition --to legacy_usd
  --drain-timeout-s 900 --idempotency-key revert-<drill id> …` (`--dry-run` first) … rerun
  under the same key … Proof (the database half; the box half below is an operator step no test
  runs): `…test_reversal_pg.py::…`".
- **rollback.md drill step 4.** Adds "(after 3b, without `ACCOUNTING_REGIME` and
  `ACTIVE_RATE_CARD_VERSION`: the target runs `legacy_usd`)" and "Then the reinstall rule …:
  `55-runtime-login.sh` only when the target carries R127's dedicated logins; 4226315 and
  bda1586 do not, so never after them".
- **rollback.md drill step 6.** "steps 3-5 again with `RELEASE=…`" → "step 3, then … W7f's
  `credit-transition --card …` … under a **new** key …, then step 4 and 5 with `RELEASE=…` and
  its own `INFRX_SET` …, with `55-runtime-login.sh` right after its 50-install". Step 3b is
  deliberately not repeated.

## Commands (worktree root unless noted; `api` = `apps/infrx-api`)

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | synced |
| fails-before, root: `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/ops/test_runbook_reversal.py` with the base (9b21339a) rollout.md and rollback.md put back temporarily | 1 | 2 failed. rv01: rollback.md step 4 has no `credit-transition --to legacy_usd`. rv02: no reinstall rule in §3 |
| api: `pytest -q tests/g/ops/test_reversal.py tests/g/ops/test_transition.py` | 0 | 9 passed |
| api: each new mutant through `mutants.run_mutant` | — | 3/3 `killed` ("1 failed, 60 deselected") |
| api: the 3 mutants applied by hand to `infrx/…`, `INFRX_D_TASK=revoke pytest -q tests/g/ops/test_reversal_pg.py`, original bytes restored | 1 ×3 | 3/3 killed (reasons in the table above) |
| api: `INFRX_D_TASK=revoke uv run --frozen --no-sync pytest -q tests/g/ops` | 0 | **96 passed** (2 warnings) |
| api: `INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/g/ops/test_mutants.py` | 0 | **118 passed** in 326 s: every mutant killed, including the 3 new ones; list well formed; every case covered; the self-tests pass |
| api: `uv run --frozen --no-sync ruff check` on the 5 new or edited test files | 0 | all checks passed (the first commit had one F401; fixed in `fb681526`) |
| root: `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/ops tests/integration/backend/recovery/test_runbooks.py` | 0 | **40 passed** (rv01, rv02, and rb03/rb08/rb12/rb13 still green) |
| api: `pytest -q` on every `tests/i/test_*.py` except the four i8-stack files and `test_mutants.py` | 0 | **152 passed** |
| api: `pytest -q tests/i/test_mutants.py -k "well_formed or every_case"`, plus each `login_*` anchor counted in the edited step 55 | 0 | 2 passed; the 7 anchors each occur exactly once |
| `bash -n infra/rollout/steps/55-runtime-login.sh` | 0 | parses |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS; 942 links across 262 documents |
| `git diff --stat 9b21339a..fb681526` | 0 | 8 files, owned paths only (listed above) |
| tests/i with its docker cases | 0 | see §"i8" below: 238 passed, 1 xfailed |

## i8

While this lane worked, `docker ps` showed `infrx-i8-postgres-supabase` and an
`infrx-i8-*-migrated-supabase` belonging to another lane. Polling every 60 s found the harness
free at 07:19Z, and the docker cases then ran here:

| Command | Exit | Result |
|---|---|---|
| api: `uv run --frozen --no-sync pytest -q tests/i` (all cases, i8 stack included) | 0 | **238 passed, 1 xfailed** (the E4C-RUNBOOK-2 reference count) |
| api: `INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/i/test_mutants.py -k login_` | 0 | **7 passed**: every step-55 mutant still killed after the header edit |

## Wiring requests

1. **`apps/infrx-api/infrx/operations/transition.py` (F-1)**: in `apply`, replace
   `result, _ = await op._once("transition", idempotency_key, reason, None, {...}, write)` /
   `return result` with `result, replayed = …` / `return {**result, "replayed": replayed}`,
   as `set_flag` does. Composed-result test: in `test_reversal_pg.py`, assert
   `replay == {**down, "replayed": True}` and `down["replayed"] is False`. Update the two
   existing equalities as well: `test_transition_pg.py` (`(again_code, again) == (0, result)`
   becomes `{**result, "replayed": True}`) and `test_transition.py`
   (`apply(w, store) == result`). Also update rollout.md §3's "(no `replayed` field on this
   verb)".
2. **Durable monitor DSN (coordinator)**: if the rerun rule is not enough, create the SSM leaf
   `/model-inference/monitor_database_url` (the `infrx_monitor` DSN, from a 0600 file), which
   preflight already reads. A reinstall then keeps `MONITOR_DATABASE_URL` without step 55, and
   the same leaf serves O4's `MONITOR_DSN_PARAM`. This is a window item alongside the two
   password parameters (15-pending-inputs).

## Open issues

- The box half of the reversal (restoring a `legacy_usd` release: 85-known-good-box.sh, its
  checkout and 50-install, then the journey) has never run. It stays an operator step.
- The known-good `schema_proof` reaches only 0023 (rollout.md §3), so the drill's target is not
  KNOWN-GOOD at `--applied 0025` until that proof is extended. Pre-existing and unchanged.
- F-1, above.
- The monitor DSN is still dropped by a reinstall until step 55 runs (wiring request 2).

## Remaining effort

Optimistic 0.1 h, likely 0.3 h, pessimistic 1 h; confidence medium. Basis: everything is
committed and green, tests/i docker cases included. What remains is review and a fix loop if
review finds something.

## Verification log

- 2026-09-26: written at implementation head fb681526; the commands above ran in this worktree.
