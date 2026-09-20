# F2 (Python half) — rulings R20–R23

## Task and status

| Field | Value |
|---|---|
| Task | F2-py — one small pass applying the coordinator's rulings on the four open requests |
| Owner | Claude Opus 5 (1M context) implementation session |
| Status | **implemented** (not integrated; fakes only, no real service, no console check) |
| Oracles claimed | F-CONTRACT, F-BASE |
| Extends | `F2-py-11607aa.md` (revision r1). Nothing in that report is retracted; two of its conformance cases were **strengthened** because they did not detect their own defect |

## Source

| Field | Value |
|---|---|
| Base SHA | `fab9fbe` |
| Implementation SHA | `2aeca2f` |
| Commits in this pass | `b8a52bc` R20–R23 · `2aeca2f` the two cases made to bite |
| Branch / worktree | `codex/f2-contracts-py` in `.claude/worktrees/codex-f2py` |
| Rulings applied | `research/plan/08-contracts-v1-encoding.md` §10 R20–R23 at `f1f1f9d` (R16–R19 are console-only) |

## Rulings as implemented

### R20 — per-phase deadlines

| Instant | Derived | Enforced by | Record |
|---|---|---|---|
| `preparation_deadline_at` | at admission | the reaper (M/W's preparation loop reads it) | `Admission` |
| `queue_deadline_at` | at the **first** durable `queued` transition, nullable before it | `claim` refuses a job past it; `recover` expires it `queue_wait_expired` | `Admission` |
| `generation_deadline_at` | at claim | `recover` terminalizes a running attempt past it as `deadline_exceeded`, even while its lease is live | `Lease` |
| `first_token_deadline_at` | at claim | the **worker** (01 leaves TTFT policy with W); the store only persists it | `Lease` |

Every instant is `min(database clock + budget, deadline_at)` (`_phase_deadline`), the
budgets come from the admission snapshot (R4) and never from current configuration,
and a prepublication requeue never moves `queue_deadline_at` (R5). A record validator
keeps `first_token_deadline_at <= generation_deadline_at`. `stall_s` deliberately has
**no** instant: an inter-event stall is a sliding bound measured from the last event,
with no fixed moment to persist — noted in `contracts/README.md`.

Three cases, plus a record-level test:
`dur_output__phase_deadlines_are_persisted_at_each_transition` (derived at the right
transition, immune to a `retune` after admission, unchanged by a requeue, and the
second attempt's generation instants derived at *its* claim),
`dur_output__no_phase_deadline_outlives_the_accepted_deadline` (a 5 s job gets
`deadline_at` for every phase, and the reaper uses it),
`dur_output__the_generation_deadline_ends_a_running_attempt` (lease TTL 10 000 s,
generation 30 s: terminal at 31 s with a live lease, no debit),
`test_fixtures.py::test_phase_deadline_instants_live_on_the_records`.

### R21 — billable causes

`BILLABLE_CAUSES` is exactly `completed`, `client_cancelled`,
`client_disconnected`; `PLATFORM_FAILURE_CAUSES` is now **defined as its
complement**, so the two partition `TerminalCause` and no future cause can become
billable by omission (asserted in `test_fixtures.py`). `_terminalize` routes:

1. published output with unknown usage → `held_unknown`, reservation held, released
   `released_platform_absorbed` after the fenced 24 h — **whatever the cause**;
2. otherwise a non-billable cause, or a billable one with no usage → hold released,
   `released_free` when the customer was never going to be charged (`invalid_media`,
   `preparation_failed`, `queue_wait_expired`, nothing ever ran) or
   `released_platform_absorbed` when we did the work and ate it (`sync_deadline`,
   `deadline_exceeded`, `engine_error`, `lost_after_publication`, …);
3. a billable cause with authoritative usage → the debit, with the existing
   over-envelope rewrite unchanged.

`dur_settle__only_three_causes_can_charge` drives **all thirteen** causes with the
same authoritative usage (1200/340): the three billable ones debit
`price_snapshot.debit(1200, 340)` exactly, and the ten others charge nothing while
releasing the hold — including `sync_deadline` with authoritative usage, as the
ruling requires. Behaviour change worth D's attention:
`lost_after_publication` used to release immediately and now reconciles for 24 h,
which is what R21's "held_unknown when usage is unknown" says;
`dur_output__loss_after_publication_is_a_terminal_failure` asserts it and the
release afterwards.

### R22 — `upload_expired`

`errors.UploadExpired` / code `upload_expired`, 410 `gone_error`, fixed message
"The upload window has expired."; `finalize_upload` no longer raises
`result_expired`. `error_codes.json`'s `http` section is **27** codes and
`error_envelopes.json` carries the new envelope (the fixture test requires an
envelope for every public code). Case:
`media_sec__an_expired_upload_window_says_so`.

### R23 — `resolve_ambiguous`

`external_id` stays a keyword: required for `adopt_provider_evidence`,
`invalid_request` for `release_reservation` (releasing a reservation while naming a
batch would discard the one fact that says the batch may still be running). Covered
inside `judge_budget__an_ambiguous_run_is_resolved_only_by_an_operator`, which also
checks the run stays `ambiguous` after the refusal.

## Cases that had to change (and why)

- `dur_fence__a_stale_generation_is_rejected`,
  `dur_output__prepublication_retries_are_bounded` and
  `dur_fence__a_stale_worker_cannot_append` now pass
  `queue_wait_interactive_s=10_000`. Under R20 a lost 120 s lease takes an
  interactive job past its persisted queue deadline, so it can no longer be
  reclaimed — correct for a customer who has already been told to give up, but not
  what those three cases are about. The docstrings say so.
- `conformance/services.py::_lease` mints the two new `Lease` instants, as
  `JobStore.claim` does.
- **Two cases were not detecting their own defect** and were strengthened in
  `2aeca2f`: `dur_output__loss_after_publication_is_a_terminal_failure` accepted any
  of three settlement states (now exactly `held_unknown` → hold held → release after
  24 h), and `dur_output__phase_deadlines_are_persisted_at_each_transition` guarded
  its requeue assertions with `if outcome is None` while the default 10 s queue
  budget expired the job first, so they never ran.

## Commands

All from `apps/infrx-api` unless stated, UTC 2026-09-20.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `uv sync --frozen --all-extras` | 0 | 41 packages checked from the committed lock |
| 2 | `uv run --frozen pytest -q` | 0 | **477 passed**, 2 warnings, 2.05 s |
| 3 | `uv lock --check` | 0 | 44 packages resolved; lock unchanged |
| 4 | `make api-test` (repo root) | 0 | 477 passed |
| 5 | `git diff --stat fab9fbe --` the four baseline test files, `gateway.py`, `infrx/{auth,media,gateway,usage}` | 0 | **empty** — byte-identical |
| 6 | `uv run --frozen pytest -q` over the four baseline files | 0 | 29 passed |
| 7 | `uv run --frozen pytest -q tests/contracts/test_conformance.py` | 0 | 118 passed (**100 cases** + 8 runners + 8 protocol checks + 2 registry guards) |
| 8 | `pkgutil.walk_packages` over `infrx` | 0 | 37 modules imported, **no** optional dependency loaded with all extras installed |
| 9 | per-fix regression matrix for this pass: revert one R20–R23 mechanism at a time in a throwaway export and run its case | 0 (script) | **8/8 fail without their fix**, 0 unexpected passes |

Counts: 100 conformance cases (was 95), 39 fixtures, 27 HTTP error codes (was 26),
477 tests (was 470), 29 baseline tests untouched. `make console-test` /
`console-lint` still **not run** (the console half's path), `make bench-test`
reports "not run" by design.

## Limits

- Fakes only: **implemented**, never integrated. D must persist the instants in the
  same transaction as the transition, and DUR-OUTPUT against real PostgreSQL is what
  proves it.
- The generation deadline is enforced by `recover`; nothing here proves W stops
  generating at it, nor that W honours `first_token_deadline_at` — that is W's suite.
- `stall_s` has no persisted instant (see above); if the coordinator wants one, it is
  a record change plus D.
- `released_free` versus `released_platform_absorbed` for the newly non-billable
  causes is this encoding's distinction, not the ruling's wording; the debit is zero
  either way and the case accepts both labels. Recorded in `contracts/README.md`.

## Changes

`infrx/contracts/records.py` (cause partition, the four phase instants, the lease
validator) · `infrx/contracts/errors.py` (`upload_expired`, `UploadExpired`) ·
`infrx/contracts/ports.py` (R23 wording) · `infrx/contracts/fakes/state.py`
(`_phase_deadline`, instants persisted at admission/prepared/claim, `claim` and
`recover` comparing them, the R21 settlement routing) ·
`infrx/contracts/fakes/media.py`, `judge.py` (R22, R23) ·
`infrx/contracts/conformance/{jobs,services}.py` (5 new cases, 6 changed) ·
`fixtures/v1/{admission,admission_replay,lease,error_codes,error_envelopes}.json` ·
`tests/contracts/test_fixtures.py` · `infrx/contracts/README.md` (R20–R23 rows, the
**"What D1 must persist"** table, no open amendment requests).

Not touched: `apps/app/**`, `pyproject.toml`, `uv.lock`, `.python-version`, the root
`Makefile`, `.gitignore`, `08-contracts-v1-encoding.md`, `gateway.py`,
`infrx/{auth,media,gateway,usage}`, any other track's files.

## Handback

- **Next:** the coordinator integrates `codex/f2-contracts-py` with the console half,
  diffs `money_cases.json` against the console copy (expected identical) and
  `error_codes.json` against `ERROR_CODE_HTTP_STATUS` (now 27 codes — the console
  half must add `upload_expired`), then reruns `make api-test` on the merged SHA.
- **For D1:** `contracts/README.md` §"What D1 must persist" lists every record change
  since the first F2 pass — `budgets`, the four phase instants, feedback
  `name`/`value`/`comment` — plus the R21 note that `sync_deadline`,
  `deadline_exceeded` and `queue_wait_expired` never carry a debit.
- **Open questions:** none. Every amendment request this track raised has been ruled
  on and implemented.

## Verification log

- 2026-09-20: R20–R23 applied on `codex/f2-contracts-py` from base `fab9fbe`,
  implementation `2aeca2f`. Phase instants persisted at each transition and enforced
  by `claim`/`recover`; billable causes reduced to three with the complement defined
  as platform-absorbed; `upload_expired` added (27 HTTP codes); `external_id`
  required or refused per resolution. Five new conformance cases and six changed;
  each of the eight mechanisms proved by reverting it alone and watching its case
  fail, including two cases that were found not to detect their own defect and were
  strengthened before the proof. 477 tests pass, 29 baseline tests byte-identical,
  `uv.lock` unchanged, no optional dependency imported (37 modules walked). Console
  tests/lint and the benchmark suite are pending, not passing; every conformance pass
  is against fakes, so the task is implemented and not integrated. No cloud, GPU,
  container, paid provider or production resource was touched.
