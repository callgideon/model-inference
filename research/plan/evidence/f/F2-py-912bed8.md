# F2 (Python half) — review r4: 14 survivors closed, a runner that cannot lie, R37–R39

## Task and status

| Field | Value |
|---|---|
| Task | F2-py — close the r4 review's four blocking findings and apply rulings R37–R40 |
| Status | **implemented** (fakes only; not integrated, no real service, no console check) |
| Oracles claimed | F-CONTRACT, F-BASE |
| Reviewed head | `f395bce` (verdict `fix_required`: 14 surviving mutants, a runner that could report a false kill, half-done skip reporting, four inaccurate evidence claims) |
| Base SHA | `fab9fbe` |
| Implementation SHA | `912bed8` |
| Commits | `41a1ebf` runner + skips · `3fe24f3` B1/B4 + R37–R39 · `912bed8` mime mutant aim |
| Rulings | §10 R37–R40 at `46a53e0` |

Every number below is quoted from the command output in the **Commands** section; none is
hand-typed.

## B2 — the runner could report a false kill (fixed first, because it gates the rest)

`run_mutant` now returns an `Outcome`, and a **kill requires all three** of: pytest exit
`1`; at least one failing test; every failing test id naming one of that mutant's own
cases. Exit codes 2–5 (interrupted, internal, usage, nothing collected) and failures
outside the named cases are `broken_runner`; a missing anchor or an undeclared case is
`misdeclared`. Both fail the run exactly as a survivor does, so the headline "N/N killed"
can no longer include a mutant that only broke the copy.

Six self-tests exercise the outcomes deliberately, plus a positive control:

| Self-test | Expected | Why |
|---|---|---|
| syntax error (`async def admit(self)) :::`) | `broken_runner` | the reviewer's first reproduction |
| import-time `NameError` | `broken_runner` | the second |
| no-op edit (a comment) | `survived` | the runner must not invent a kill |
| lethal edit under the **wrong** case name | `survived` | a kill must come from the named case; this is why it is a survivor and not a `broken_runner` |
| missing anchor | `misdeclared` | the list must match the code |
| no case declared | `misdeclared` | R32 |
| `heartbeat_stores_the_callers_lease` | `killed`, "1 failed" | the positive control |

The reviewer's related note is also handled: the two mutants that died on **untyped**
errors now die on typed ones. `admit_accepts_a_negative_hold` is killed by
`dur_cap__a_negative_maximum_hold_is_refused`, which asserts
`errors.http_status(exc.code) in (400, 402)` on a `DomainError`; `judge_reserve_negative`
by `judge_budget__settlement_amounts_are_validated_money`, which asserts the code rather
than letting `http_status` raise a `LookupError`. Both paths go through
`fakes/support.money_input`, which converts `money.parse`'s `ValueError` into
`invalid_request` at the boundary.

## B3 — skip reporting was half done

Twelve cases still read hooks through `harness.extra.get(...)` or
`if harness.failures is None: return` and silently dropped assertions. Now **every**
optional read goes through the single `hook()` helper — including `failures`, `jobs`,
`stream`, `queued`, `crash`, `content_budget`, `reap`, `put_object`, `attach`, `text` —
which raises `MissingHook`; pytest reports a skip naming the hook, and `run_cases`
refuses to treat one as a pass unless the caller collects them. The conditional
assertions are now unconditional, so a case either runs whole or skips. The four judge
cases that hard-failed with `KeyError 'available'` now skip. The stale
`conformance/harness.py` docstring is rewritten.

`test_a_hookless_factory_skips_and_never_silently_passes` drives every suite with a
factory stripped of all optional hooks and of `failures`, asserts
`ran + skipped == len(cases)`, and prints the report so evidence quotes it:

```
$ uv run --frozen pytest -q tests/contracts/test_conformance.py -k hookless -s
  engine: 1 skipped
  feedback: 6 skipped
  jobstore: 14 skipped
  judge: 10 skipped
  mediastore: 7 skipped
  scheduler: 1 skipped
  streamstore: 17 skipped
  tracesink: 5 skipped
```

The full report names the hook per case (e.g.
`('dur_admit__a_refused_admission_reserves_nothing', 'journal_bytes')`). Every
streamstore and feedback case needs `jobs` (they must admit a job first), which the test
allows explicitly while still forbidding a suite that *passes* entirely on no hooks.
`test_the_fakes_skip_nothing` is the other half: with the real factories, `skipped == []`
for all eight suites, so the headline counts mean what they say.

## B1 — 14 surviving mutants, each now killed by a named case

| Reviewer's id | Invariant | New case | Mutant |
|---|---|---|---|
| n15 | the hold is checked against **available**, not the ledger | `dur_cap__a_hold_is_checked_against_available_not_the_ledger` (a second 0.007 hold on a 0.01 wallet is `402`; available never negative) | `balance_checked_against_the_ledger` |
| p3 | R29 binds `complete` | `dur_fence__a_deadline_binds_append_and_complete` (31 s into a 30 s budget, live lease: terminalized `deadline_exceeded`, no debit) | `deadlines_not_applied_on_complete` |
| p2 | R29 binds `append` | same case, through the `stream` hook | `deadlines_not_applied_on_append` |
| n03 | published output buys no extra time | same case, parametrized `published ∈ {False, True}` | `deadlines_skipped_once_published` |
| n14 | no terminal event **anywhere** in a batch | `dur_output__no_terminal_event_anywhere_in_a_batch` (four batch shapes; then a cancel leaves exactly one real terminal chunk) | `terminal_check_only_on_the_first_event` |
| n11 | the tombstone TTL runs from the terminal state | `dur_admit__the_tombstone_ttl_runs_from_the_terminal_state` (250 s attempt, replay 86,350 s after terminal) | `tombstone_measured_from_admission` |
| n12 | any committed chunk is publication | `dur_output__any_committed_chunk_is_publication` (progress, usage, error) | `only_a_delta_publishes` |
| n17 | `expire` ignores a caller's clock | `dur_output__expiry_never_runs_on_a_callers_clock` (`+365 d` prunes nothing) | `expire_trusts_the_caller` |
| n23 | `settle` is refused from `ambiguous` | `judge_budget__an_ambiguous_run_cannot_be_settled_directly` | `settle_allowed_from_ambiguous` |
| n30 | a flush leaves open captures' bytes | `trace_bounds__a_flush_leaves_open_captures_alone` | `flush_zeroes_open_captures` |
| m20 | a dropped `finish` releases its charge | `trace_bounds__a_dropped_finish_releases_its_charge` | `dropped_finish_keeps_its_charge` |
| n31 | a calibration label belongs to the **row's** tenant | extended `feedback_ack__an_operator_may_label_a_calibration_set` (an ORG_B operator labels an ORG_A row) | `label_uses_the_operators_org` |
| n36 | a claimed candidate is not re-indexed | `dur_outbox__a_claimed_candidate_is_not_re_indexed` | `enqueue_ignores_inflight` |
| n06 | a published overdue job reconciles | `dur_settle__a_published_job_past_its_deadline_reconciles` | `published_deadline_released_at_once` |

The R7 sweep the finding asked for: every port operation was re-checked for caller time.
`recover()` takes none; `expire(now)` clamps to the store clock (its case now proves it);
`flush(deadline)` uses the injected clock and ignores the argument; `TraceSink.reap`
takes a grace period, not a time; nothing else accepts one.

## B4 — evidence corrections, and one real defect behind them

- **"Four duplicate guards removed" was wrong — three were.** `fakes/scheduling.py` has a
  zero-line diff for that pass; `rebuild` still clears `inflight`/`acknowledged`. Rather
  than restate it, the mutant is re-aimed at `pending` (`rebuild_loses_jobs`, killed) and
  the clearing lines are covered by `dur_outbox__a_claimed_candidate_is_not_re_indexed`
  and `dur_outbox__rebuild_restores_every_queued_job_exactly_once`. The corrected count
  is **three**.
- **"Early returns are now hook(...)" was false for 12 cases** — fixed in B3 above.
- **"Staging stays all-or-nothing" was false.** `stage` raised `NotFound` on an
  unresolvable upload *after* storing an inline sibling. It now validates **and resolves**
  every reference into a pending map and makes one visible step
  (`self.objects.update(pending)`), so nothing is stored unless everything can be. The
  case drives all three refusal kinds (oversize, another tenant's, unresolvable upload)
  and the corrected retry; three mutants cover it (`staging_commits_as_it_goes`,
  `staging_resolves_after_writing`, `staging_validates_nothing_up_front`).

## Rulings applied

- **R37** — no trace operation raises into the request path. `open` on `off`/`minimal`
  returns a **no-op capture** (`add` False, `finish` keeps nothing), so G needs no branch;
  `finish`/`abandon` are idempotent; a mismatched envelope is dropped and counted
  `malformed`; `TraceCapture` is a context manager whose `__exit__` abandons an unfinished
  capture and propagates the request's own exception; `add` is synchronous, O(1), no disk.
  `TraceSink.reap(grace_s)` releases the bytes of captures still open past the job's
  `deadline_at` plus the grace period and counts them under the new
  `TraceLossReason.abandoned` (enum value, `trace_envelope_abandoned.json` fixture,
  `test_an_abandoned_capture_is_an_honest_envelope`, four cases, six mutants). `crash()`
  followed by a late `abandon` cannot drive the counter negative (`max(0, …)` on every
  release path, asserted in the reaping case).
- **R38** — the queue budget is cumulative time **in** `queued`. `queue_wait_used_s` is
  persisted on `Admission`; `_enter_queued` sets
  `queue_deadline_at = min(now + budget − used, deadline_at)` and `_leave_queued` charges
  the interval. Running time is the generation budget's, so the three retry cases
  (`dur_fence__a_stale_generation_is_rejected`,
  `dur_output__prepublication_retries_are_bounded`,
  `dur_fence__a_stale_worker_cannot_append`) **no longer override the queue budget** —
  an ordinary interactive job retries after a 120 s lease loss on default limits, which is
  the point of the ruling. `dur_output__queue_time_is_time_spent_queued` walks 40 s
  queued → 31 s running → requeue → 20 s running → requeue → expiry, asserting the used
  time, the remainder and that it never grows.
- **R39** — `_terminalize` calls `check_terminal_capacity` before any wallet, outcome or
  reservation mutation (`_Journal.check` is `store(dry_run=True)`, so it raises exactly
  what the write would). `dur_settle__a_settlement_that_cannot_journal_moves_no_money`
  uses a 16-byte reservation and asserts the balance, the state and the reservation rows
  are untouched. The commit ordering D must follow — *commit the terminalization, then
  return the typed refusal; never raise inside the transaction that would roll it back* —
  is in `contracts/README.md`.
- **R40** is the merge criterion, not work: the corpus below is what it will be measured
  against.

## Nonblocking applied

`accepted_mime=5` (or `None`) is a typed `400` rather than a `TypeError` out of the port,
with two mutants. `claim`'s absolute-deadline check is **kept with a comment** saying it
is a deliberate duplicate guard: `queue_deadline_at` is capped by `deadline_at` and fires
first for every job `prepared` has queued, so a mutation of that line alone is
equivalent — this is the "say which" the coordinator asked for.

## Mutation runner

```
$ uv run --frozen python tests/contracts/mutants.py --list | tail -1
157 mutants over 123 named cases

$ uv run --frozen python tests/contracts/mutants.py   # full list
157/157 killed

$ make api-mutants (INFRX_MUTANTS=all)
167 passed in 81.81s (0:01:21)
real 82.11s
exit=0
```

167 = 157 mutants + 6 runner self-tests + the positive control + 3 list/coverage guards.
No survivors, no `broken_runner`, no `misdeclared`.

## Commands

```
$ uv sync --frozen --all-extras
Checked 41 packages in 0.34ms
exit=0

$ uv run --frozen pytest -q
534 passed, 2 warnings in 9.89s
exit=0

$ uv lock --check
Resolved 44 packages in 0.92ms
exit=0

$ uv run --frozen pytest -q tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/test_app_factory.py
29 passed, 2 warnings in 1.29s
exit=0

$ uv run --frozen pytest -q tests/contracts/test_conformance.py
153 passed in 0.59s

$ uv run --frozen pytest -q tests/contracts/test_mutants.py
19 passed in 7.65s

$ make api-test
534 passed, 2 warnings in 9.81s
exit=0

$ git diff --stat fab9fbe -- <4 baseline tests> gateway.py infrx/{auth,media,gateway,usage}
(no output = byte-identical) exit=0

$ counts
modules walked: 38 | optional deps loaded: none
conformance cases: 131
fixtures: 40 | http codes: 27
```

`make console-test` / `console-lint` **not run** (the console half's paths);
`make bench-test` reports "not run" by design.

## Record and port changes for D1, T1, G and W

- **D1** — `Admission`: `budgets`, `preparation_deadline_at`, `queue_deadline_at`,
  **`queue_wait_used_s`** (R38: seconds in `queued`, updated on leaving it, with the
  deadline recomputed from the remainder on entering). `Lease`:
  `generation_deadline_at`, `first_token_deadline_at`, renewed from the **stored** row.
  `Feedback`: `name`/`value`/`comment`, `calibration_set` only through
  `label_calibration`, whose tenant is the **row's** (R26). Commit ordering: R39's
  terminalize-then-refuse. Capacity checks — including the terminal event's journal bytes
  — before any wallet, outcome or reservation write.
- **T1** — `TraceSink.open() -> TraceCapture` with `add`/`finish`/`abandon`, a context
  manager, idempotent close, no-op captures for `off`/`minimal`, and a `reap` the sink
  runs on a timer; `TraceLossReason.abandoned` is a new value for the projection.
  Nothing in the trace path may raise into the request path.
- **G** — derives `deadline_at` from the budgets (admission refuses one it cannot keep),
  maps `upload_expired` (410), and uses the capture as a context manager in `finally`.
- **W** — enforces `first_token_deadline_at` (the store only persists it), and must
  expect `already_terminal` from `append`/`complete` once a phase deadline has passed:
  the store terminalizes in that same call.

## Limits

Fakes only: implemented, never integrated. The mutation list covers the fakes, not the
records (those are covered by `test_fixtures.py`). Engine cases are exempt from mutation
coverage by declaration (they script an external process, not our state).
`first_token_deadline_at` is persisted but not enforced here. Single store-wide lock in
`FakeJobStore` (unchanged `ponytail:` note). A real adapter must run these suites **and**
its own mutation list: these guards are not its guards.

## Open questions

None. R1–R40 plus 01/02 decided everything this pass needed.

## Verification log

- 2026-09-20: r4 pass, base `fab9fbe`, implementation `912bed8`. The runner's kill
  criterion was tightened first (exit 1 + a failure + every failing id named), with six
  self-tests and a positive control, because every other claim depends on it. All 14
  reviewer survivors now die by a named case; skip reporting routes every optional hook
  through `hook()` with a printed hookless-factory report; `stage` was made genuinely
  all-or-nothing after the evidence claim proved false, and the "four duplicate guards"
  claim is corrected to three. R37, R38 and R39 implemented with cases and mutants;
  R38 let the three retry cases drop their widened queue budgets. Quoted above:
  534 tests pass, 157/157 mutants killed in 82 s, 29 baseline tests byte-identical,
  `uv.lock` unchanged, 38 modules walked with no optional dependency loaded, 131
  conformance cases, 40 fixtures, 27 HTTP codes. Console tests/lint and the benchmark
  suite are pending, not passing. No cloud, GPU, container, paid provider or production
  resource was touched.
