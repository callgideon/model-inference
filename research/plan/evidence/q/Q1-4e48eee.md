# Q1 — Deterministic memory scheduler and fairness model

## Task and status

| Field | Value |
|---|---|
| Task | Q1 — Deterministic memory scheduler and fairness model (`research/plan/handoffs/Q-scheduling.md`) |
| Owner / session | Scheduling track (Q), Claude Opus 5 implementation session |
| Status | **implemented, not integrated** — the adapter passes the exported `run_scheduler_conformance` suite against the shared fakes and its own regression, property and mutation suites. Nothing is wired into a composition root, no Valkey, no real service, no deploy. Q2/Q3 remain planned. |
| Oracles | F-CONTRACT (the exported suite against the real adapter), DUR-OUTBOX (replay, visibility, rebuild, "the index never authorizes execution") |

## Source

| Field | Value |
|---|---|
| Base SHA | `8744418bb762458e947e09acaa4e09251ff4327f` |
| Implementation SHA | `4e48eee6bf563ab49901a610e6822bec268c4dcb` |
| Integrated SHA | none — integration is the coordinator's step |
| Branch / worktree | `codex/q1-memory-scheduler` in `.claude/worktrees/codex-q1` |
| Commits | `33bc565` adapter, `287faf9` conformance/behaviour/property tests, `9a2ee77` mutation list, `4e48eee` failure drill |

## What was built

`apps/infrx-api/infrx/scheduling/memory.py` — `MemoryScheduler`, a `ports.Scheduler`
adapter with the operations of the frozen port (`enqueue`, `claim_candidate(worker,
kind=…)`, `acknowledge`, `remove`, `rebuild`) plus two non-port observability helpers
(`depth()`, `stats()`, and `tags()` for tests and Q2's differential run). Design
choices that are decisions rather than mechanics:

1. **Start-time fair queuing per `(dispatch kind, org)` flow.** FIFO inside a flow (by
   arrival sequence), and across flows the smallest `(virtual tag, arrival sequence of
   the candidate)` wins. That pair is a *total* order, so no dict or set iteration order
   can influence a dispatch.
2. **The tag advances at DISPATCH**, by `service_cost(event) / weight(org)`. The
   `03 §2.3` Lua sketch advances it at enqueue; that charges a tenant for work it only
   asked for (a burst that is then cancelled leaves a penalty behind). The brief pins
   dispatch, and `test_q1_fair__the_virtual_finish_time_advances_at_dispatch_only`
   asserts both halves of it.
3. **Virtual time, not wall time.** The dispatched flow's start tag becomes the index's
   virtual time; an arriving flow starts there. No fairness decision reads the clock, so
   fairness cannot drift with how fast time moves or how long a test sleeps (nothing
   sleeps). The injected clock is used only for `available_at` and visibility.
4. **Separate streams per dispatch kind (R52),** including separate fairness state:
   preparation and inference are different worker pools, so a tenant's transcodes must
   not push it back in the GPU line. `kind=None` still takes whatever is next across
   both; an unknown kind is `invalid_request` (R55).
5. **Visibility follows the lease of the pool that was fed** (R52):
   `preparation_lease_ttl_s` (30 s) for `prepare_dispatch`, `lease_ttl_s` (120 s) for
   `inference_dispatch`, measured **from the claim**, never from the event's own
   timestamp.
6. **Bounded memory:** `max_items` 500 and `max_bytes` 256 MiB (`03 §2.5`, from llm-d's
   `maxRequests`/`maxBytes`), charged per candidate with `codec.compact_bytes`. A full
   index raises `capacity_exhausted` with `Retry-After` — a refusal the outbox drain can
   distinguish from "already indexed", and never a silent drop. **Index depth is not
   admission capacity**, and `stats()` says so in its docstring.
7. **The service cost is provisional (`1.0` s per event, injectable).** `03 §2.4`'s EWMA
   estimator does not exist yet; with equal costs the algorithm degenerates to weighted
   round robin, which is conservative rather than wrong. Marked `est.` here, not in any
   research table.

### Rebuild semantics (the contracts leave these open; this adapter pins them)

| Aspect | Decision | Why |
|---|---|---|
| pending | becomes exactly the snapshot, de-duplicated by `event_id`, in snapshot order | PostgreSQL is the truth about what is queued |
| caps | **not applied** during a rebuild | refusing part of the truth would turn a brief loss of throughput into a permanent one |
| in-flight | **cleared** | a pre-rebuild claim was only a hint; its holder's right to execute came from `JobStore.claim`, whose generation/lease fences a second attempt (demonstrated in the drill: the returning worker gets `not_claimable`) |
| acknowledged | **cleared** | keeping the set would silently drop a job PostgreSQL still reports as queued (a requeued job whose earlier candidate was acknowledged). Replay safety after a rebuild comes from the snapshot, and duplicate *execution* is still impossible because the index never authorizes execution |
| fairness state | **restarts** (virtual time 0, only the snapshot's tenants exist) | no tenant inherits a credit or a penalty from an index that no longer exists, and a tenant absent from the snapshot keeps nothing |

## Requirement coverage

Test ids are as pytest prints them under `tests/q/`. Seven of them are the exported
cases of `infrx.contracts.conformance.services.scheduler_cases()` run against the real
adapter through the coordinator's harness (same `FakeClock`, `SequentialIds`, wallet
hooks and shared `FakeJobStore`; only `Harness.port` differs).

| Test id | Invariant it demonstrates |
|---|---|
| `test_q1_contract__the_real_adapter_passes_the_exported_case[dur_outbox__a_candidate_carries_its_dispatch_kind]` | R52: a preparation pool is fed from the index and never handed an inference candidate; R55: an unknown kind is a typed 400, not an empty index; the index carries only dispatch kinds |
| `…[dur_outbox__enqueue_is_replay_safe]` | at-least-once delivery of one stable `event_id` indexes exactly one candidate |
| `…[dur_outbox__a_claimed_candidate_is_not_re_indexed]` | a replay while the first copy is in flight does not hand one job to two workers; after the visibility timeout it is one candidate again; after the ack, none |
| `…[dur_outbox__the_index_never_authorizes_execution]` | claiming a candidate mutates no durable job state (job still `queued`, no outcome); `JobStore.claim` mints generation 1 |
| `…[dur_outbox__acknowledged_candidates_do_not_come_back]` | an acknowledgment removes the candidate for good and blocks re-indexing |
| `…[dur_outbox__a_lost_worker_returns_its_candidate]` | visibility is a timeout from the **claim**: a candidate that waited longer than the lease TTL is still handed to one worker at a time |
| `…[dur_outbox__rebuild_restores_every_queued_job_exactly_once]` | losing the index loses throughput, never jobs; no pre-rebuild in-flight entry returns later |
| `test_q1_contract__the_whole_suite_runs_and_skips_nothing` | R32: the suite ran 7/7 with 0 skips against this adapter (printed, quoted below) |
| `test_q1_contract__the_adapter_satisfies_the_scheduler_protocol` | every port operation exists and is `async`, as the ports table declares |
| `test_q1_fair__tenants_alternate_and_ties_break_on_arrival` | equal weights round-robin the tenants; equal tags are separated by arrival order, not by org id or dict order |
| `test_q1_fair__a_noisy_tenant_cannot_delay_a_peer_past_one_round` | **acceptance**: with a 30-deep noisy tenant, each peer's first dispatch is inside the first `N` slots and consecutive dispatches are ≤ `N` slots apart (`N` = tenants) |
| `test_q1_fair__weight_buys_a_proportional_share` | a tenant weighted 3 receives 9 of the first 12 dispatches against an equal-depth peer |
| `test_q1_fair__the_virtual_finish_time_advances_at_dispatch_only` | enqueue does not move the tag; one dispatch moves it by exactly `cost/weight`; a cancelled burst leaves no penalty |
| `test_q1_fair__an_idle_tenant_accumulates_no_credit_and_a_backlogged_one_is_not_overtaken` | **acceptance**: an absent tenant returns level with its peer (one slot each, not a run of catch-up), and the served tenant is not pushed behind for ever |
| `test_q1_fair__a_candidate_that_waited_catches_up_once_and_cannot_hoard` | a candidate that was not yet available gets exactly one slot of catch-up, then its flow is pulled up to the index's virtual time |
| `test_q1_config__a_weight_that_would_break_dispatch_is_refused_where_it_is_set` | 0, negative, NaN and infinite weights are refused at construction (they are a division by zero or a poisoned comparison inside dispatch) |
| `test_q1_kind__preparation_and_inference_are_separately_fair` | R52: three preparation dispatches for a tenant do not cost it its turn on the inference pool |
| `test_q1_kind__a_lost_preparation_worker_returns_its_candidate_on_the_preparation_lease` | a prepare candidate returns after 30 s while an inference candidate does not, and returns after 120 s |
| `test_q1_kind__returned_candidates_keep_their_arrival_order` | FIFO inside a tenant survives a lost worker: the returned candidate is re-offered before the two that arrived behind it |
| `test_q1_caps__a_full_index_refuses_with_a_typed_retryable_error` | the item cap answers `capacity_exhausted` (429, `Retry-After`), holds the count at the cap, and an ack frees a slot |
| `test_q1_caps__queued_bytes_are_counted_per_candidate_and_returned` | byte accounting is per candidate and symmetric over ack and cancellation; the byte cap binds independently of the item cap |
| `test_q1_cancel__removal_drops_pending_and_in_flight_candidates_and_their_flow` | **acceptance**: cancellation removes pending and in-flight candidates, a removed in-flight candidate never returns, and empty/cancelled tenants retain no fairness state |
| `test_q1_index__a_stale_candidate_never_executes` | DUR-OUTBOX: a job cancelled while its candidate was in flight is refused by `JobStore.claim`; the same dispatch event delivered again after removal is re-indexed (the index is not the authority on cancellation) and still cannot execute |
| `test_q1_rebuild__clears_in_flight_and_acknowledged_and_keeps_no_stale_tenant` | the rebuild semantics table above, including the duplicate-in-snapshot case |
| `test_q1_rebuild__recovery_is_not_bounded_by_the_index_caps` | a 4-event snapshot rebuilds into an index whose item cap is 1 |
| `test_q1_stats__depth_and_waiting_age_are_reported_without_claiming_capacity` | depth is published per dispatch kind; the waiting age counts only available candidates |
| `test_q1_stats__a_candidate_is_not_offered_before_it_is_available` | `available_at` bounds dispatch without hiding the tenant's other work |
| `test_q1_drill__losing_the_index_mid_flight_loses_no_job_and_executes_none_twice` | the failure drill below |
| `test_q1_support__the_harness_supplies_the_hooks_the_suite_documents` | the factory really hands the cases the shared fake JobStore and the wallet hooks, and really runs the suite against `MemoryScheduler` rather than the fake |
| `test_q1_property__identical_input_gives_an_identical_dispatch_order` | **acceptance**, seeded (1, 2, 3, 5, 8, 13, 21, 34): two fresh indices fed one 80-step script agree on every dispatch, on their final tags and on their accounting |
| `test_q1_property__the_index_is_work_conserving_and_loses_nothing` | at every step, a claim returns a candidate exactly when the shadow model says one is available; afterwards the drain yields every live candidate exactly once and the emptied index keeps no state |
| `test_q1_property__a_noisy_tenant_cannot_starve_a_bounded_peer` | **acceptance** as a bound in dispatch slots: peer gaps ≤ number of tenants over a 20–60-deep noisy backlog, per seed |
| `test_q1_property__rebuild_after_enqueue_is_the_same_index_as_rebuild_alone` | **acceptance**: `rebuild ∘ enqueue` is idempotent — a running index and one rebuilt from the same snapshot have the same tags, accounting and dispatch order, and `rebuild` twice changes nothing |
| `tests/q/test_mutants.py` (33 ids) | R32: the list is well formed (each anchor appears **exactly once**), all 27 mutants are killed, and 5 self-tests prove the runner cannot report a false kill |

## Environment

| Field | Value |
|---|---|
| Classification | local development worktree; no cloud, no paid provider, no container, no network |
| OS / kernel | Linux 7.0.0-1010-aws x86_64 |
| Python | 3.12.3 in the pinned `apps/infrx-api/.venv` (`uv sync --frozen --all-extras`, i.e. `make api-env`) |
| Tooling | `uv 0.11.8`, `pytest 8.4.2`, `pydantic 2.13.5`; no pytest plugins (`asyncio.run` throughout) |
| Services | none. The `valkey` extra is installed but unused by Q1; `VALKEY_URL` unset means the memory scheduler (`08` §5) |
| Environment variables read | none by this module. `PilotSettings` is passed in; `infrx/config.py` is the only environment reader and was not touched |
| Seeds | property suite seeds `(1, 2, 3, 5, 8, 13, 21, 34)`, 80-step scripts, `random.Random(seed)` only |

## Commands

Exact commands, in the pinned environment. `make` targets are run from the repo root,
`uv` commands from `apps/infrx-api`. Times are UTC on 2026-09-21.

| # | Command | Exit | Completed | Output tail |
|---|---|---|---|---|
| 1 | `make api-test` | 0 | 06:26:18Z | `736 passed, 2 warnings in 53.61s` |
| 2 | `uv run --frozen pytest -q tests/q` | 0 | 06:25:24Z | `66 passed in 74.83s (0:01:14)` |
| 3 | `uv run --frozen pytest tests/q/test_memory_scheduler.py -k contract -q -s` | 0 | 06:30:44Z | `scheduler conformance: 7 ran, 0 skipped []` then `9 passed, 20 deselected in 0.16s` |
| 4 | `uv run --frozen python tests/q/mutants.py` | 0 | 06:27:19Z | `27/27 killed` |
| 5 | `uv run --frozen pytest -q tests/q/test_mutants.py` | 0 | 06:27:48Z | `33 passed in 21.35s` |
| 6 | `make api-mutants` (the shared list, unchanged by Q1) | 0 | 06:23:54Z | `276 passed in 178.79s (0:02:58)` |

Not run, and why:

- `make console-test` / `console-lint` / `console-typecheck` / `console-mutants`: **not
  run** — Q1 changes no console file.
- `make bench-test`: **not run** — E1 owns `models/marlin2b/tests`.
- Real-service (Layer 2) tests: **not applicable** to Q1. The Valkey adapter, its
  container (`infrx-q2-valkey`, port 56379 per `08` §8) and the randomised differential
  run against this model are Q2's deliverable. Nothing here claims integration.
- Baseline byte-identity: **not applicable** — Q1 adds only new files under
  `infrx/scheduling/` and `tests/q/`; `git diff --stat 8744418..HEAD` shows 7 files,
  1504 insertions, 0 deletions, and no F1 path among them. Command 1 is the baseline run
  (735 pre-existing tests still pass, plus Q1's).

## Results

Command 2, the focused suite (66 tests: 9 contract, 20 behaviour/drill, 4 property,
33 mutation):

```
66 passed in 74.83s (0:01:14)
```

Command 3, the exported suite against the real adapter, printed by the test itself so
the count is output rather than prose (7 cases, 0 skipped — a skip would be reported
here, never counted as a pass, R32):

```
scheduler conformance: 7 ran, 0 skipped []
..
9 passed, 20 deselected in 0.16s
```

Command 4, the mutation list (one pytest process per mutant, only the named cases
selected; `1 failed, 65 deselected` means exactly the named case noticed):

```
[killed       ] tie_break_prefers_the_newest_arrival: 1 failed, 65 deselected in 0.39s
[killed       ] the_fair_choice_takes_the_largest_virtual_time: 1 failed, 65 deselected in 0.44s
[killed       ] the_tag_does_not_advance_at_dispatch: 1 failed, 65 deselected in 0.41s
[killed       ] the_tag_ignores_the_weight: 1 failed, 65 deselected in 0.44s
[killed       ] the_dispatch_start_is_not_clamped_to_the_virtual_time: 1 failed, 65 deselected in 0.36s
[killed       ] a_non_positive_weight_is_accepted: 1 failed, 65 deselected in 0.38s
[killed       ] fairness_state_is_shared_across_dispatch_kinds: 1 failed, 65 deselected in 0.41s
[killed       ] visibility_is_measured_from_the_event_time: 1 failed, 65 deselected in 0.41s
[killed       ] a_claim_does_not_record_when_it_happened: 1 failed, 65 deselected in 0.42s
[killed       ] the_preparation_lease_times_out_every_kind: 1 failed, 65 deselected in 0.34s
[killed       ] returned_candidates_lose_their_arrival_order: 1 failed, 65 deselected in 0.32s
[killed       ] enqueue_forgets_the_acknowledged_ids: 1 failed, 65 deselected in 0.33s
[killed       ] enqueue_reindexes_a_candidate_already_in_the_index: 2 failed, 64 deselected in 0.35s
[killed       ] the_kind_filter_is_inverted: 1 failed, 65 deselected in 0.32s
[killed       ] an_unknown_kind_is_swallowed: 1 failed, 65 deselected in 0.33s
[killed       ] a_candidate_is_offered_before_it_is_available: 2 failed, 64 deselected in 0.33s
[killed       ] the_item_cap_is_not_enforced: 1 failed, 65 deselected in 0.33s
[killed       ] the_byte_cap_is_not_enforced: 1 failed, 65 deselected in 0.33s
[killed       ] bytes_are_never_returned: 1 failed, 65 deselected in 0.32s
[killed       ] depth_is_not_reported_per_kind: 1 failed, 65 deselected in 0.32s
[killed       ] cancellation_keeps_the_candidates: 1 failed, 65 deselected in 0.32s
[killed       ] cancellation_makes_the_index_authoritative: 1 failed, 65 deselected in 0.32s
[killed       ] an_empty_tenant_keeps_its_fairness_state: 1 failed, 65 deselected in 0.32s
[killed       ] rebuild_keeps_the_in_flight_entries: 1 failed, 65 deselected in 0.32s
[killed       ] rebuild_keeps_the_acknowledged_ids: 1 failed, 65 deselected in 0.32s
[killed       ] rebuild_does_not_restart_the_fairness_epoch: 1 failed, 65 deselected in 0.33s
[killed       ] acknowledge_is_synchronous: 1 failed, 65 deselected in 0.32s

27/27 killed
```

Failures and skips, reported separately as the format requires: **no failing test and
no skipped test in any command above.** Two mutants failed during development and are
recorded because they changed the deliverable rather than being quietly dropped:

1. `cancellation_makes_the_index_authoritative` **survived** the first run. The defect
   (remembering a cancelled candidate as acknowledged) was invisible because
   `test_q1_index__a_stale_candidate_never_executes` re-enqueued a *freshly built* event
   after the removal, so a new `event_id` slipped past the acknowledged set. An outbox
   replay carries the **same** stable id, so the test was weaker than the invariant it
   claimed. The test now re-enqueues the same event object, and the mutant is killed.
   This is R32 doing its job on a case of mine, not a tooling artefact.
2. `an_arriving_tenant_starts_at_zero` **survived** and was **removed from the list**
   with its reason recorded in the list and in `_flow`'s docstring, because the edit is
   provably behaviour-preserving, not unobserved: an unserved flow's tag can only lie at
   or below the virtual time and `claim_candidate` clamps it back up before charging, and
   a flow created later can never hold a smaller arrival sequence than one created
   earlier (sequences are assigned at enqueue, and a flow's creation coincides with its
   first enqueue). Both variants therefore produce the same order on every workload. The
   invariant the corresponding case claims is killed by
   `the_dispatch_start_is_not_clamped_to_the_virtual_time` and
   `the_tag_does_not_advance_at_dispatch`.

## Failure drill

`test_q1_drill__losing_the_index_mid_flight_loses_no_job_and_executes_none_twice`, run
against the shared `FakeJobStore` (the executable specification of `02`):

| Step | Injection | Durable state (JobStore) | Index state |
|---|---|---|---|
| 1 | four jobs admitted and `prepared`, one candidate each indexed | all four `queued`, asserted `["queued"] * 4` | 4 candidates |
| 2 | `worker-lost` claims a candidate, then the process is assumed lost | unchanged, still `queued` | 3 pending, 1 in flight |
| 3 | the index is **destroyed** (a fresh `MemoryScheduler`) and rebuilt from the queued snapshot | unchanged, still `queued` | `rebuild(...) == 4` |
| 4 | the new pool drains and claims each job | all four `running`, asserted `["running"] * 4`; **every `Lease.generation == 1`**, so nothing executed twice | empty, no fairness state left |
| 5 | `worker-lost` returns and calls `JobStore.claim` for its stale candidate | unchanged | — |

Outcome: the stale claim is refused with `not_claimable` (asserted on the code, not on
the exception type), so the index losing its memory costs throughput and one wasted
claim, never a job and never a second execution. Retry/duplicate behaviour: the
candidate that was in flight at the loss is simply gone from the index and comes back
through the snapshot, which is why `rebuild` returns 4 rather than 3. Cleanup: nothing
to clean — no file, container, port or external resource is touched by any Q1 test.

## Artifacts

Raw command output, kept in the session scratch area (git-ignored, as R15 requires
until I2 provisions a durable evidence store), sha256:

| File | sha256 |
|---|---|
| `q1/api-test.txt` | `06f485e6b0f2d6d8d987cfe13fbf73a299c233a5f8b60643cd37b29348843958` |
| `q1/q-suite.txt` | `433f588d81c7d2d263806c438d81f9ae3412adfd82e2491e2a3c36f3793c9e5a` |
| `q1/q-conformance.txt` | `5eb2070a192ad775d9f46c6449525c5d7971a059096728ec90e8bcc362487868` |
| `q1/q-mutants.txt` | `74e051ce291701ddfad94b83b4bbce0f506620bbbdcf7a02e1e8b00c5109cb9e` |
| `q1/q-mutants-pytest.txt` | `79c5047ea4e00e5b3ca10b1cdf30fb59827397f566c028ea382d4d68b593bb06` |
| `q1/api-mutants.txt` | `30f5fb691ef7d317c89391ed7f06b5cf6ec0ca8acebe0b89b14ae07c73f92e5b` |

No credential, customer prompt, signed URL or storage key appears in any of them; the
suites use the conformance builders' synthetic organizations and a fake engine.

## Changes

Owned paths only; 7 files, +1504, −0 (`git diff --stat 8744418..HEAD`):

```
 apps/infrx-api/infrx/scheduling/__init__.py        |   6 +
 apps/infrx-api/infrx/scheduling/memory.py          | 366 +++++++++++++
 apps/infrx-api/tests/q/mutants.py                  | 273 +++++++++++
 apps/infrx-api/tests/q/support.py                  |  59 +++
 apps/infrx-api/tests/q/test_fairness_properties.py | 256 ++++++++++
 apps/infrx-api/tests/q/test_memory_scheduler.py    | 466 ++++++++++++++++++
 apps/infrx-api/tests/q/test_mutants.py             |  78 ++++
```

Nothing outside `apps/infrx-api/infrx/scheduling/`, `apps/infrx-api/tests/q/` and this
report was touched: no contract, no fake, no conformance suite, no `config.py`, no
manifest, no lock file, no composition root, no `Makefile`. No contract revision is
requested. Nothing in `infrx/scheduling/` is named `queue`.

Migration / deploy / rollback: **no schema change, no migration, no deployment
artefact.** The module is imported by nothing yet, so rollback is deleting the
directory (or reverting the four commits); there is no durable state to drain, no
feature flag to unset and no retention obligation attached to it. When it is wired,
`VALKEY_URL` unset already means "memory scheduler" (`08` §5), so a rollback from Q2's
Valkey adapter to this one is a restart plus a `rebuild` from PostgreSQL, which is the
protocol the drill above exercises.

## Limits

- **Not integrated, and not claimed to be.** Every dependency is a shared fake. No
  gateway, worker or console path calls this adapter yet.
- **No real-service evidence** (Q2's job): no Valkey, no container, no randomised
  differential run against a server, no persistence or replication behaviour verified.
  The handoff's warning stands — asynchronous Valkey replication is not a no-loss
  durable queue, and nothing here has tested it. **pending**, owner Q2.
- **The service-cost estimator is a constant** (`1.0` s, `est.`). Until `03 §2.4`'s EWMA
  exists, fair shares are per request, not per predicted second, so a tenant sending
  120 s clips and one sending 10 s clips are treated alike (`03 §2.3` measured ~11×
  spread in prompt tokens). The injection point exists; the estimator is Q3/W work.
- **No per-org weight source.** `weights` defaults to `{}` (every tenant 1.0). A
  configured weight needs the settings field requested below; the priority *bands* of
  `03 §2.3` (`interactive`/`standard`/`free`) are deliberately **not** implemented —
  `IndexEvent` carries no band, and inventing one would be a contract change.
- **Two invariants cannot carry a mutant by construction**, and are named here rather
  than left implied: `test_q1_rebuild__recovery_is_not_bounded_by_the_index_caps` and the
  "claiming mutates no durable state" half of `test_q1_index__…` assert the *absence* of
  code, so no single edit produces them (the same reason the shared list exempts the
  engine cases). `test_q1_property__identical_input_gives_an_identical_dispatch_order` is
  a regression guard rather than a mutant-killable invariant: any deterministic
  single-edit defect leaves two in-process runs equal, so it can only fail for an
  implementation whose order depends on process-varying state (hash order, a set, a wall
  clock) — which is exactly what it is there to catch.
- **Concurrency assumption:** single event loop, no lock. Every operation is one
  synchronous critical section with no `await` inside it, which is what makes "one event
  is never handed to two workers" hold; a threaded host must add a lock. Stated in the
  class docstring, untested for threads because nothing threads it.
- **`O(pending)` scans** in candidate selection and in the returned-candidate re-sort,
  bounded by `max_items` (500). Marked with a `ponytail:` comment naming the heap
  upgrade. Unmeasured: no benchmark exists for a 500-deep index yet (E4 territory).
- **The acknowledged-id set grows** until a `rebuild` clears it. At pilot volumes it is
  a set of UUID strings; Q3's reconciler is the natural place to prune it against
  terminal jobs.
- **`tests/q/mutants.py` imports `tests/contracts/mutants.py`** for `Mutant`, `Outcome`,
  `Result` and `_failing_ids` (a coordinator-owned helper, read-only) because the
  coordinator's runner is hard-wired to `tests/contracts/test_conformance.py`. If that
  module moves, Q's mutation suite fails loudly rather than silently; the alternative was
  copying the outcome vocabulary and the "only a kill counts" rule, which is exactly the
  duplication R32 exists to avoid.
- **The Q mutation list runs in the default `make api-test`** (+~21 s). If the
  coordinator prefers the shared pattern (subset by default, everything under
  `make api-mutants`), that is the integration request below; Q1 deliberately did not
  edit the root `Makefile`.

## Handback

**Next unblocked task:** Q2 — Valkey adapter with atomic tested scripts (starts after
Q1). It ports this model to namespaced keys and scripts with every accessed key
declared, and must pass the same `run_scheduler_conformance` suite plus a randomised
differential run against `MemoryScheduler`; `tags()` and `stats()` exist for exactly
that. Q3 stays blocked on D2/D3.

**Integration requests** (all outside Q's owned paths, none urgent — nothing imports
the module yet):

1. `infrx/contracts/limits.py` + `infrx/config.py` (coordinator-owned): add
   `PilotSettings` fields `max_index_items: int = 500` and
   `max_index_bytes: int = 268_435_456` (env `MAX_INDEX_ITEMS`, `MAX_INDEX_BYTES`;
   provenance `research/production-api/03` §2.5). Q1 takes them as constructor arguments
   with those defaults (`infrx.scheduling.MAX_INDEX_ITEMS`/`MAX_INDEX_BYTES`), so the
   change is one keyword at the construction site and a two-line `08` §5 addition. Not a
   contract revision: no record or port shape changes.
2. Composition root (`infrx/gateway/app.py`, and W's worker entry point when it exists),
   at the point where a scheduler is first needed — **not** requested for Q1 on its own:
   `from infrx.scheduling import MemoryScheduler` and
   `MemoryScheduler(lambda: datetime.now(timezone.utc), limits=settings)` when
   `settings.valkey_url` is empty (`08` §5: "`VALKEY_URL` unset ⇒ memory scheduler"),
   with Q2's adapter behind the same name when it is set. Q1 exposes no router, so there
   is nothing to `register(app, rt)`.
3. Optional, root `Makefile`: if the Q mutation list should be gated like the shared one,
   add `tests/q/test_mutants.py` to the `api-mutants` target and Q will gate
   `tests/q/test_mutants.py` behind `INFRX_MUTANTS` in the same change. Q1's default is
   "always runs", on the grounds that a mutation suite which usually does not run is a
   mutation suite that does not work.

**Unresolved findings:** none open. The two mutant survivors found during the task are
resolved above (one test strengthened, one mutant retired with a proof and a comment).

## Verification log

- 2026-09-21: Authored from the commands above at implementation SHA `4e48eee`. Every
  count and summary line is quoted from captured output; no number in this report was
  typed by hand from memory. Nothing is claimed as integrated or live-verified.
