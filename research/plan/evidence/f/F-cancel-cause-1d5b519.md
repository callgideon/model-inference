# F cancel-cause — `JobStore.cancel(..., *, cause)` (evidence)

## Task and status

- **Task:** F cancel-cause. This is a short track-F contracts lane ahead of D5 item 3 and G2 item 4, and it is G2's "D-new" request. It makes the additive cancel-cause port change that D5's SQL and G2's relay both need.
- **Owner:** Opus 5.5 implementation session. Fable coordinates.
- **Status: implemented.**
  - The fakes and the conformance cases are complete.
  - The PostgreSQL adapter accepts the keyword and refuses, with a typed error, any cause that 0016 cannot record. This was run on this lane's own PostgreSQL.
  - The change is **not integrated**. Real per-cause settlement on PostgreSQL waits for D5's 0018.

## Source

| | |
|---|---|
| Base (integration head, `git fetch origin && git merge --ff-only origin/claude/backend-impl` → "Already up to date") | `a237d6f36b4ba371c50578ca330938db2d005072` |
| Implementation SHA | `1d5b519aaf168c4e35eceb762064fa67b6bb8efa` |
| Branch / worktree | `codex/f-cancel-cause` / `.claude/worktrees/codex-fcancel` |
| Commits (`git log --oneline a237d6f..1d5b519`) | `9f41066` item 1, `9192da9` item 2, `1d5b519` item 3 |

## Per-item table

| Item | Commit | Killing case(s) | Mutants (contracts list, `tests/contracts/mutants.py`) | Oracle / ruling |
|---|---|---|---|---|
| 1. Port: `cancel(org_id, job_handle, *, cause: TerminalCause = client_cancelled)`, with a docstring listing the causes a caller may pass (`records.CANCEL_CAUSES` = `client_cancelled`, `client_disconnected`, `sync_deadline`) and saying that anything else is `InvalidRequest`. `CreditJobStore` has no `cancel`: cancel is shared, as its docstring already says. | `9f41066` | `tests/contracts/test_cancel_cause.py::test_dur_settle__cancel_takes_a_keyword_cause_that_defaults_to_client_cancelled`. The case is keyword-only with the default `client_cancelled` on the port, the fake and `PgJobStore` (the last two added in items 2/3), and the set is exactly the three causes. | `cancel_port_default_is_not_the_client` (ports.py default → `client_disconnected`); `cancel_causes_widened` (records.py adds `completed`; also killed by the item-2 refusal case) | DUR-SETTLE, R21 |
| 2. `FakeJobStore.cancel` records the given cause, and `_terminalize` settles by it | `9192da9` | `dur_settle__cancel_records_its_cause_and_settles_by_r21` (jobstore; each cause × unpublished/published, the fenced 24 h release, ledger untouched); `dur_settle__cancel_refuses_any_other_cause_and_changes_nothing` (every other `TerminalCause` plus a non-cause string gets `InvalidRequest`; the job stays non-terminal, the balance is unchanged, and the job then cancels normally); `credit_settle__cancel_records_its_cause_and_settles_by_r21` (credit_jobstore: each cause × unpublished/published on the CREDIT wallet, USD wallet untouched) | `cancel_cause_dropped` (fake records `client_cancelled` whatever it is given); `credit_cancel_cause_dropped` (same edit, named on the CREDIT case); `cancel_unknown_cause_mapped_to_client_cancelled` (the refusal becomes `cause = client_cancelled`); `cancel_accepts_any_cause` (`if cause not in CANCEL_CAUSES:` → `if False:`); `cancel_sync_deadline_charged_to_the_client` (`sync_deadline` added to `BILLABLE_CAUSES`, so it settles as the client's `released_free`); `cancel_client_disconnected_absorbed_by_the_platform` (`client_disconnected` dropped from `BILLABLE_CAUSES`) | DUR-SETTLE, CREDIT-SPEND, R21 |
| 3. `PgJobStore.cancel` accepts the keyword; until 0018 it refuses any cause other than `client_cancelled` before SQL | `1d5b519` | `tests/contracts/test_cancel_cause.py::test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record` (no database: a stub connection records every statement) | `pg_cancel_records_an_unsupported_cause` (`state/jobstore.py` refusal → `if False:`: 0016 would then record `client_cancelled`) | DUR-SETTLE, R21 |

**Settlement per cause.** The fake is the reference and the conformance cases pin this behaviour. A cancel never carries usage.

| Cause | State | Unpublished | Published |
|---|---|---|---|
| `client_cancelled` (default) | `cancelled` | `released_free` | `held_unknown` → `released_platform_absorbed` after the fenced 24 h |
| `client_disconnected` | `cancelled` | `released_free` | same as above |
| `sync_deadline` | `cancelled` | `released_platform_absorbed` | same as above |
| anything else | unchanged | `InvalidRequest`, nothing written | `InvalidRequest`, nothing written |

This matches D5 brief item 3 ("disconnected is free unpublished and `held_unknown` published; `sync_deadline` is platform-absorbed") and D5's `d5_sync_deadline_released_free` mutant.

**Pg error type (item 3): `errors.UnsupportedParameter`, with `param="cause"`, code `unsupported_parameter`.**

- **Why that class:** `UnsupportedParameter` is a subclass of `InvalidRequest`.
  - The port's promise still holds: a refused cause is an `InvalidRequest`, and one `except errors.InvalidRequest` covers both refusals.
  - Its own code lets a caller tell "this store cannot record that cause yet" apart from "that is not a cancel cause".
  - It is a typed `DomainError`, so it is the contract's answer, and a kill under R83 needs no `dies_by`.
- **What the refusal guarantees:** it is raised before any SQL, so nothing is written, and 0016 never silently turns the cause into `client_cancelled`.
- **Why not `NotImplementedError`:** it is untyped, so it is neither the port's answer nor an R83 kill.
- **Why not `DependencyUnavailable`:** a retry cannot help.
- **Why not `InternalError`:** it would break the port's single refusal type for causes.
- **Caution:** `unsupported_parameter` maps to HTTP 400, so a route must never answer a client with it. For G2 the client has already gone or timed out when cancel runs.

## Requirement coverage

- **DUR-SETTLE / R21:** `cancel(..., cause=)` records the given cause in state `cancelled`.
  - Before any output, the client's causes are `released_free` and `sync_deadline` is `released_platform_absorbed`.
  - After publication, every cause is `held_unknown` with the hold held, then `released_platform_absorbed` after the fenced 24 h.
  - The ledger never moves. The committed outcome (`get_owned`) equals the one returned.
- **DUR-SETTLE / R21:** any other cause (all 11 other `TerminalCause` members plus a non-cause string) is `InvalidRequest`. The job stays non-terminal and the balance is identical; the same job then cancels with the default.
- **CREDIT-SPEND / R21:** the same per-cause settlement holds on the CREDIT wallet.
  - After the unpublished cancels, the reserved total equals the three published holds and the CREDIT ledger has not moved.
  - After the fenced 24 h the wallet equals its starting state.
  - The v1 debit is 0, and the USD wallet is ledger 0 / reserved 0.
- **Additive:** the keyword is keyword-only and defaults to `client_cancelled` on the port and on both adapters. Every existing `cancel(org, handle)` caller is unchanged:
  - W `tests/w/test_loop.py`
  - Q `tests/q/test_memory_scheduler.py`, `q3differential.py`, `test_reconcile.py`
  - `infrx/operations/service.py`
  - D `test_e3b_drills.py`, `test_lease_units.py`
- **Pg before 0018:** `client_disconnected`, `sync_deadline`, `completed` and `"bogus"` are each refused with `UnsupportedParameter`, `param == "cause"`, and zero statements are sent. The default still sends exactly one statement and returns the committed outcome.

## Environment

- **Machine:** Linux 7.0.0-1010-aws (dev host). Local only: no hosted project, AWS or pilot box.
- **Tooling:** Python 3.12.3 (`apps/infrx-api/.venv`, `make api-env` → `uv sync --frozen`), uv 0.11.8, Docker 29.6.2.
- **Item-3 PostgreSQL:** `postgres@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20` (postgres 16.14, the D harness's plain image plus `supabase_shim.sql`).
  - Container `infrx-fcancel-postgres` on `127.0.0.1:55538`, database `infrx_fcancel`.
  - Used from a **scratch copy** (`git archive HEAD apps/infrx-api apps/app/supabase` into the session scratchpad), with one line of `tests/d/pgharness.py` patched to that name, port and database. The worktree's harness was not edited.
  - The container is removed at exit; `docker ps -a | grep -c fcancel` → `0`.
- **Ports never touched:** 55432–55436.

## Commands and results (UTC 2026-09-23; env var names only)

1. **`make api-env`** → exit 0.

2. **Focused, per item:**
   ```
   uv run --frozen python -m tests.contracts.mutants cancel_port_default_is_not_the_client cancel_causes_widened cancel_cause_dropped credit_cancel_cause_dropped cancel_unknown_cause_mapped_to_client_cancelled cancel_accepts_any_cause cancel_sync_deadline_charged_to_the_client cancel_client_disconnected_absorbed_by_the_platform pg_cancel_records_an_unsupported_cause
   ```
   Exit 0:
   ```
   [killed       ] cancel_port_default_is_not_the_client: 1 failed, 763 deselected in 0.91s
   [killed       ] cancel_causes_widened: 2 failed, 762 deselected in 0.77s
   [killed       ] cancel_cause_dropped: 1 failed, 763 deselected in 0.81s
   [killed       ] credit_cancel_cause_dropped: 1 failed, 763 deselected in 0.79s
   [killed       ] cancel_unknown_cause_mapped_to_client_cancelled: 1 failed, 763 deselected in 0.73s
   [killed       ] cancel_accepts_any_cause: 1 failed, 763 deselected in 0.82s
   [killed       ] cancel_sync_deadline_charged_to_the_client: 3 failed, 761 deselected in 0.81s
   [killed       ] cancel_client_disconnected_absorbed_by_the_platform: 2 failed, 762 deselected in 0.80s
   [killed       ] pg_cancel_records_an_unsupported_cause: 1 failed, 763 deselected in 1.01s

   9/9 killed
   ```
   The pristine baseline over the whole list's union of cases ran inside that call (R83 amendment (b)).

3. **Folded contracts list, detached:**
   ```
   INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py
   ```
   Exit 0 (the merged-tree figure before this lane was 415; the 9 new mutants make up the difference):
   ```
   424 passed in 953.99s (0:15:53)
   ```

4. **`uv run --frozen pytest -q tests/contracts`** (no lists: the default mutant subset) → exit 1:
   ```
   3 failed, 1048 passed in 83.04s (0:01:23)
   ```
   - **The 3 failures are `tests/contracts/v2/test_v1_projection_pg.py` and are not a defect.** Each is `HarnessBusy: another run holds /tmp/infrx-d1-postgres-55432.lock (... checkout .../codex-e3b2)`. That module uses D1's shared port 55432, and the harness refused while altering nothing.
   - **They were rerun on this lane's own port instead,** in the scratch copy (`TMPDIR=<scratch>/tmp .venv/bin/python -m pytest -q -p no:cacheprovider -rfEs tests/contracts/v2/test_v1_projection_pg.py`). The rerun was exit 0, `3 passed in 2.46s`, after two retries (see Limits: ephemeral port).

5. **Pytest orderings, excluding the shared-port module above:**
   ```
   uv run --frozen pytest -q -p no:randomly tests/test_*.py tests/contracts --ignore=tests/contracts/v2/test_v1_projection_pg.py
   ```
   → exit 0, `1077 passed, 2 warnings in 91.26s`.
   ```
   uv run --frozen pytest -q tests/contracts tests/test_*.py --ignore=tests/contracts/v2/test_v1_projection_pg.py
   ```
   → exit 0, `1077 passed, 2 warnings in 92.20s`.

6. **Callers of `cancel` on the fakes:**
   ```
   uv run --frozen pytest -q tests/w/test_loop.py tests/q/test_memory_scheduler.py tests/g/ops --ignore=tests/g/ops/test_mutants.py -rs
   ```
   → exit 0, `132 passed in 1.53s`.
   - D's no-database adapter units, `uv run --frozen pytest -q tests/d/test_lease_units.py tests/d/test_adapter_units.py` → `30 passed in 0.38s`.

7. **Item 3 on this lane's PostgreSQL (scratch copy, `TMPDIR=<scratch>/tmp`):**
   1. **As merged, without a partition entry:**
      ```
      pytest -p no:cacheprovider -rfEsx -v tests/d/test_leases.py tests/d/test_jobstore_conformance.py -k cancel
      ```
      → exit 1: `1 failed, 4 passed, 79 deselected, 1 xpassed in 18.65s`.
      - Passing: `test_cancel__every_state_terminalizes_and_releases_in_one_transaction` and `test_races__claim_heartbeat_and_cancel_serialize_on_the_job_row`, plus `dur_settle__cancelling_after_publication_reconciles` and the new `dur_settle__cancel_refuses_any_other_cause_and_changes_nothing`. The XPASS is the RACY cancel/complete case.
      - The one failure is the new `dur_settle__cancel_records_its_cause_and_settles_by_r21`, as designed: `UnsupportedParameter: unsupported_parameter: cause <TerminalCause.client_disconnected: 'client_disconnected'> needs migration 0018 (D5)`.
      - This is why integration request 1 exists.
   2. **With integration request 1's `PENDING` line applied in the scratch copy only:**
      ```
      pytest -q -p no:cacheprovider -rfEsx tests/d/test_leases.py tests/d/test_jobstore_conformance.py tests/d/test_lease_units.py tests/d/test_adapter_units.py
      ```
      → exit 0: `86 passed, 3 skipped, 25 xfailed, 1 xpassed in 39.85s`.
      - The new case is a strict XFAIL with the D5 reason.
      - The skips are `SKIPPED [3] tests/d/test_jobstore_conformance.py:114: missing optional hook 'stream' (not a pass)`, which is D4's journal and pre-existing.

**Not run:**

- `make check` / `make api-test`: the D, Q and E suites use other lanes' ports and containers (55432–55436, `infrx-q3-valkey`).
- `tests/q/test_reconcile.py`: it needs Q3's Valkey. It was attempted once and failed with `infrx-q3-valkey did not accept connections within 90.0s`. That container was created at 05:51:45Z by another run, before this lane first touched `tests/q` (05:59:48Z). The harness took its inspect-only path and did not create, start, stop or remove it. Its only `cancel` call is positional, so the keyword cannot affect it.
- The Supabase image (`INFRX_D1_IMAGE=supabase`): this lane has no SQL change.
- `make integration`: `tests/integration` was not touched.
- Console targets: `apps/app` was not touched.

## Failure drill (item 3)

- **Injection point:** `PgJobStore.cancel(org, handle, cause=client_disconnected | sync_deadline | completed | "bogus")` against a connection stub that records every statement (unit case), and against the real 0016 store (conformance case).
- **Durable state before and after:** zero statements are sent, so there is no row change. On PostgreSQL the same job stays non-terminal and then cancels with the default: `dur_settle__cancel_refuses_any_other_cause_and_changes_nothing` passes on 0016.
- **Retry:** a caller that retries with the default gets 0016's `client_cancelled`, which is an explicit and visible decision.
- **Cleanup:** the `infrx-fcancel-postgres` container was removed at exit; one `Created`-state leftover from a failed bind was removed by name. It carried this checkout's label and was ours.

## Artifacts

Scratch logs are local to the session scratchpad and not durable. First 16 hex characters of each sha256:

| Log | sha256 prefix |
|---|---|
| `mutants-all.log` | `fa2c997bfd8ccd5a` |
| `mine.log` | `5560518ae75cd4aa` |
| `contracts-default.log` | `694c5abdf9362222` |
| `projection-pg.log` | `0a2462d378813381` |
| `pg-run2.log` | `4a001b0fe43daf09` |
| `pg-run3.log` | `55f8c81950a992a6` |
| `pending.diff` | `513c46c5d97a0459` |
| `order-legacy-first.log` | `4b2cd95dccc66f68` |
| `order-track-first.log` | `5f6c9180c5dc5272` |
| `callers.log` | `ea88b2f4c37d8553` |

No secrets appear: the harness DSN password is the D harness's fixed local value and was never printed.

## Changes

**Paths**, all within the lane's owned paths:
- `apps/infrx-api/infrx/contracts/ports.py`: signature and docstring.
- `apps/infrx-api/infrx/contracts/records.py`: `CANCEL_CAUSES`.
- `apps/infrx-api/infrx/contracts/fakes/state.py`: `FakeJobStore.cancel`.
- `apps/infrx-api/infrx/contracts/conformance/jobs.py`: 2 cases.
- `apps/infrx-api/infrx/contracts/conformance/v2_contracts.py`: 1 case.
- `apps/infrx-api/infrx/state/jobstore.py`: keyword plus typed refusal only.
- `apps/infrx-api/tests/contracts/test_cancel_cause.py`: new.
- `apps/infrx-api/tests/contracts/mutants.py`: 9 mutants, plus the new module as a `CONTRACTS` target.
- `apps/infrx-api/tests/contracts/test_mutants.py`: the new module in `RECORD_TESTS`.

**Contract change:** additive only. It adds a keyword-only parameter with a default and a new constant; no record or wire shape changes.

**Migration:** none. Rollback is reverting the three commits. The integration request below must then be reverted with them.

## Limits

- **Fakes only for per-cause settlement.** On PostgreSQL the other two causes are refused until D5's 0018 (P-input: none; blocked on D5 item 3).
- **The CREDIT case has not run on PostgreSQL.** D5's new `tests/d/test_credit_jobstore_conformance.py` does not exist yet.
- **Ephemeral-port collisions.** Host port 55538, like every 554xx/555xx task port, lies inside the kernel's ephemeral range (`/proc/sys/net/ipv4/ip_local_port_range` = `32768 60999`).
  - Docker's bind failed intermittently with "address already in use", three times in total, and succeeded on retry.
  - `infrx-q3-valkey` (another lane's) shows the same symptom: it is up with no published port.
  - This is a coordinator-level risk, not this lane's.

## Handback

**Next unblocked:**
- D5 item 3: SQL `infrx.cancel` in 0018.
- G2 item 4: cancellation rendering, plus the disconnect and timeout cancels with their causes.

**Integration requests:**

1. **Must be applied with this merge, or `tests/d` goes red.** Add one line to D's `PENDING` in `apps/infrx-api/tests/d/test_jobstore_conformance.py`. That file is a D5-owned partition file. The line was verified in the scratch copy, where the case XFAILs strictly:
   ```diff
   @@ -71,6 +71,10 @@
            "dur_settle__unknown_usage_is_held_then_released_as_platform_absorbed",
            "dur_settle__an_unknown_usage_hold_is_never_released_on_a_callers_clock",
            "dur_outbox__every_transition_emits_its_projection")},
   +    # F cancel-cause: 0016 records only client_cancelled, so the adapter refuses the other
   +    # two causes (UnsupportedParameter) until D5's 0018 infrx.cancel records them.
   +    "dur_settle__cancel_records_its_cause_and_settles_by_r21":
   +        "D5 item 3: 0018's infrx.cancel records client_disconnected/sync_deadline",
    }
   ```
2. **For D5, at 0018:**
   - Replace the two refusal lines in `PgJobStore.cancel` with `"cause": cause` in the `cancel` arguments.
   - Retire the mutant `pg_cancel_records_an_unsupported_cause` from the contracts list (its anchor disappears, so it would read `misdeclared`).
   - Invert `test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record` into "the cause is sent", with a D code mutant.
   - Drop the `PENDING` line above.
   - Include `credit_settle__cancel_records_its_cause_and_settles_by_r21` in the new CREDIT conformance partition.
   - `dur_settle__cancel_refuses_any_other_cause_and_changes_nothing` already passes on 0016 and must keep passing on 0018: the SQL refuses with `invalid_request` and nothing changes.
3. **Coordinator:** consider moving task ports out of the ephemeral range, or reserving them with `net.ipv4.ip_local_reserved_ports`. See Limits.

## Verification log

- 2026-09-23: Written at implementation SHA `1d5b519`. All counts and tails above are quoted from command output.
