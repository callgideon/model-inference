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
   both; an unknown kind is `invalid_request` (R55). **CORRECTED in review round 1
   (B2):** at this SHA the *flows* were per kind but the virtual time was one shared
   scalar, so the separation was incomplete and preparation traffic did erase the debt
   between inference tenants. Fixed at `9fa728f`; see round 1 below.
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
  1554 insertions, 0 deletions, and no F1 path among them. Command 1 is the baseline run.
  **CORRECTED (B5):** the pre-existing suite is **670** tests, not 735
  (`pytest -q --collect-only --ignore=tests/q` prints `670 tests collected`), and 670 + 66
  was the 736 that command 1 reported.

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
   with its reason recorded in the list and in `_flow`'s docstring. **This paragraph is
   WRONG and was CORRECTED in review round 1 (B1): the edit is not
   behaviour-preserving** - the argument below misses the case where a flow sits at
   exactly the virtual time with an older head sequence. The mutant is restored at
   `e56223f` with a case that kills it. The refuted argument, kept because history is
   not rewritten: an unserved flow's tag can only lie at
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

Owned paths only. **CORRECTED (B5):** the block first quoted here was
`git diff --stat 8744418..9a2ee77`, one commit short of the implementation SHA this
report names. `git diff --stat 8744418..4e48eee` is:

```
 apps/infrx-api/infrx/scheduling/__init__.py        |   6 +
 apps/infrx-api/infrx/scheduling/memory.py          | 366 +++++++++++++++
 apps/infrx-api/tests/q/mutants.py                  | 273 +++++++++++
 apps/infrx-api/tests/q/support.py                  |  59 +++
 apps/infrx-api/tests/q/test_fairness_properties.py | 256 ++++++++++
 apps/infrx-api/tests/q/test_memory_scheduler.py    | 516 +++++++++++++++++++++
 apps/infrx-api/tests/q/test_mutants.py             |  78 ++++
 7 files changed, 1554 insertions(+)
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

---

# Review round 1 — coordinator review of `5a8a02a`: `fix_required`

Appended, not rewritten: the body above is the round-0 report with the two false claims
the review caught marked `CORRECTED` in place. This section is the disposition of B1–B5,
the coordinator's three decisions and the nonblocking list, with the new results quoted
from output.

| Field | Value |
|---|---|
| Reviewed SHA | `5a8a02a` |
| Round-1 implementation SHA | `5a5602a` (`9fa728f` adapter, `e56223f` tests + mutants, `5a5602a` runner strengthening) |
| Status | **implemented, not integrated** (unchanged). **CORRECTED in round 2:** "every finding addressed" was true of B1-B5 and false as a whole - the B2 fix regressed unfiltered claims (B6) and its new comparison was not exercised by any test (B7). Round 2 below. |
| Suite | 51 tests in `tests/q` (was 34 plus a 32-subprocess mutation suite), 36 mutants, 36/36 killed |

## Disposition

| Item | Disposition | Where |
|---|---|---|
| **B1** the retired mutant is not equivalent | **Fixed.** The mutant is restored, the case the review supplied is a test, and `_flow`'s docstring now carries the counter-example instead of the false proof. Measured independently before believing it: over this suite's 300 seeded 80-step workloads the `tag = 0` variant changes the dispatch order on **109 seeds** (the review's own generator reports 154 of 300) | `test_q1_fair__a_newcomer_does_not_outrank_a_served_flow_with_an_older_head`, mutant `an_arriving_tenant_starts_at_zero` |
| **B2** fairness state is not separate per kind | **Fixed for kind-filtered pools** (the review confirmed both numbers). `_virtual_time` is one float **per dispatch kind**. **CORRECTED in round 2 (B6):** the claim that `(tag − virtual time of that flow's own kind, arrival sequence)` is "comparable across kinds" is **false** - a kind's virtual time only advances when that kind is dispatched, so a flow in the quiet kind keeps its lag for ever and an unfiltered worker starves it. That comparison is withdrawn by ruling R60 and replaced by two-level selection; see round 2. Both review scenarios are cases with the numbers asserted exactly | `memory.py` `_select`/`claim_candidate`, `test_q1_kind__preparation_traffic_does_not_erase_the_weighted_share` (asserts `{ORG_A: 20, ORG_B: 80}` with and without preparation traffic), `test_q1_kind__preparation_traffic_does_not_erase_a_service_time_debt` (11:1 costs, share of service seconds within 0.05 of 0.50 both ways), mutant `virtual_time_is_shared_across_dispatch_kinds` |
| **B3** (R21) vacuous rebuild dedupe | **Fixed.** The count assertion is replaced by a hand-out and a byte assertion: `rebuild((keep, keep, acked, keep)) == 2`, `stats()["bytes"] == len(compact_bytes(keep)) + len(compact_bytes(acked))`, and the drain hands each event exactly once | `test_q1_rebuild__a_duplicate_in_the_snapshot_is_indexed_and_charged_once`, mutants `rebuild_indexes_a_duplicate_twice`, `rebuild_does_not_charge_bytes` |
| **B3** (R02/R03) vacuous tie-break | **Fixed.** The new case enqueues `ORG_A` first (its id also sorts first) and cancels its oldest candidate, so the flow created first, with the lexically smaller org, has the *newer* head: creation order and org id both point the wrong way | `test_q1_fair__ties_break_on_arrival_not_on_flow_creation_or_on_the_org_id`, mutants `tie_break_ignores_arrival_order`, `tie_break_uses_the_org_id`, `tie_break_prefers_the_newest_arrival` |
| **B3** (R07) cost not injected | **Fixed.** An injected estimator of 7 s against a weight of 2 must move the tag by exactly 3.5, and the pool's virtual time by the dispatch's start | `test_q1_fair__one_dispatch_moves_the_tag_by_exactly_cost_over_weight`, mutant `the_tag_advance_ignores_the_estimator` |
| **B4** failure ordering wedges a candidate | **Fixed.** The cost is computed and validated in `_service_cost` **before** any state moves, and a bad answer (`0`, negative, NaN, infinity, or a raising estimator) is a typed `internal_error`. The case asserts `stats()` and `tags()` are *identical* to before, that the candidate is still pending, that it is dispatched on the next claim (checked after +10,000 s), and that the estimator was called exactly twice | `test_q1_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing`, mutant `a_bad_service_cost_is_not_validated` |
| **B5** evidence errors | **Fixed in place, marked `CORRECTED`.** The diffstat is now `git diff --stat 8744418..4e48eee` (1554 insertions, 516 lines in `test_memory_scheduler.py`); the baseline is **670** tests (`pytest -q --collect-only --ignore=tests/q` → `670 tests collected`), and 670 + 66 = the 736 command 1 reported; the B1 and B2 claims are corrected where they were made | §Changes, §Commands, §Results, §What was built above |
| **Decision 1** gate the list | **Done.** `tests/q/test_mutants.py` runs a three-mutant subset plus the five runner self-tests by default and the whole list under `INFRX_MUTANTS=all`; default mutant subprocesses fell from 32 to 8. Makefile line requested below | `tests/q/test_mutants.py` |
| **Decision 2** keep the shared runner import | **Kept**, read-only, with the note that Q switches to the parameterised runner when it is exported | `tests/q/mutants.py` |
| **Decision 3** configuration names | **Accepted**, integration request 1 unchanged | below |

## Nonblocking items

| Item | Disposition |
|---|---|
| bytes == Σ compact bytes after a rebuild | Done, with the mutant the review predicted would survive (`rebuild_does_not_charge_bytes`) |
| pin the visibility boundary (`>=` at exactly the TTL) | Done: `test_q1_kind__visibility_expires_at_the_ttl_not_after_it` claims at TTL − 1 µs (nothing) and at exactly the TTL (back), mutant `visibility_expires_after_the_ttl` |
| no partial insert on a refused enqueue | Done: the caps case asserts `stats()` is byte-identical after the refusal and that no flow was created for the refused tenant |
| stale acknowledge after a rebuild | Documented in `rebuild`'s docstring: it drops the rebuilt candidate until the next rebuild, benign under `JobStore` fencing, and Q3 should ack before it rebuilds |
| float tags drift | Documented on `tags()` **and deliberately not fixed**: Valkey ZSET scores are IEEE doubles, so exact rationals here would create a divergence Q2 could not reproduce. The requirement on Q2 is the same operations in the same order |
| acknowledged-id memory, global item cap | In §Limits below |

## Commands and results (round 1, UTC 2026-09-21)

Machine note, because it changes how the timings must be read: this host was running
several tracks' suites in parallel during these runs (`uptime` reported
`load average: 36.17, 28.80, 18.70` at 07:08:20Z). Wall times are therefore **not**
comparable between round 0 and round 1; the gating effect is reported as the number of
mutation subprocesses, which is load-independent, plus same-session timings.

| # | Command | Exit | Output |
|---|---|---|---|
| 1 | `make api-test` | 0 | `721 passed, 2 warnings in 40.70s` (the same command earlier in the round, under load average 36: `720 passed, 2 warnings in 102.43s` and `82.96s`; the count moved 720 -> 721 with the runner self-test added at `5a5602a`) |
| 2 | `uv run --frozen pytest -q tests/q` | 0 | `51 passed in 6.34s` (13.10s for 50 under load average 36 earlier in the round) |
| 3 | `uv run --frozen pytest tests/q/test_memory_scheduler.py -k contract -q -s` | 0 | `scheduler conformance: 7 ran, 0 skipped []` then `9 passed, 28 deselected in 0.25s` |
| 4 | `uv run --frozen pytest -q -s tests/q/test_mutants.py` | 0 | `Q mutants: 36 declared, 3 selected (default subset)` then `10 passed in 5.35s` |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q -s tests/q/test_mutants.py` | 0 | `Q mutants: 36 declared, 36 selected (INFRX_MUTANTS=all)` then `43 passed in 49.05s` |
| 6 | `uv run --frozen python tests/q/mutants.py` | 0 | `36/36 killed` |
| 7 | `pytest -q --collect-only --ignore=tests/q` / `--collect-only tests/q` | 0 | `670 tests collected in 0.57s` / `51 tests collected` (670 + 51 = the 721 of command 1) |
| 8 | own differential measurement (B1/B2), 300 seeded workloads per variant | 0 | `seeds compared: 300` / `arrival-tag=0 differs on: 109 seeds` / `shared virtual time differs on: 239 seeds` |

Test counts: 51 in `tests/q` = 9 contract (7 exported cases + the suite runner + the
protocol shape) + 4 property + 28 behaviour/drill + 10 mutation (1 well-formedness +
3 subset mutants + 6 runner self-tests). Under `INFRX_MUTANTS=all` the mutation file is
43. Default mutation subprocesses: 9 (3 mutants + 6 self-tests), down from 32. Failures:
none. Skips: none, in any command. `make console-*` and `make bench-test`
remain not run (no console or bench file is touched); real-service tests remain Q2's.

The full mutant list, quoted (command 6): every line is `killed`, and `1 failed, N
deselected` means exactly the named case noticed the defect.

```
36/36 killed
```

## Artifacts (round 1)

Raw output in the session scratch area (git-ignored, R15), sha256:

| File | sha256 |
|---|---|
| `q1/r2-api-test.txt` | `68c8dba51f274f1b8e950787096e3771f24ca1b093e41d0a96ef2b34c69a6b51` |
| `q1/r2-q-suite.txt` | `3d0e57d6be89e7ec2bedd8cd95373b297a5bb14b0795e244ae4fc77c728f30e3` |
| `q1/r2-conformance.txt` | `b4ebe6538a1c2daf56dd8b6b6454bbc6f368014a048c4a812c037651141e2dc7` |
| `q1/r2-mutants-subset.txt` | `6adfefcbe9863bfe7edb4851ec40b7631502a4f914ff68cda1397635076e4605` |
| `q1/r2-mutants-all.txt` | `d391f6f77b38201727f0b591b5d5cfe03e12e3590a9841ced46d8dccaf6c1319` |
| `q1/r2-mutants.txt` | `eefc4c1020b539894948da226714660806042ca48aea4a8707acf441b551d26d` |
| `q1/differential.py` (the B1/B2 measurement of command 8) | `27e0621122140ccdb2159250a3c69f7ec4f9fdc5ec201013ac417d3fa1285de8` |

The differential script is scratch, not a committed test: it applies each reviewed edit
to a throwaway copy of the package and compares dispatch orders over the committed
`workload(seed)` generator. The committed cases are what gate the behaviour; this only
answered "how often does it matter" without taking the review's number on trust.

## Requirement coverage added in round 1

| Test id | Invariant |
|---|---|
| `test_q1_fair__a_newcomer_does_not_outrank_a_served_flow_with_an_older_head` | B1: `A A A B B B`, three dispatches, then a new tenant dispatches `A B A B U A B`; a newcomer starting at zero would produce `A B A U B A B` |
| `test_q1_kind__preparation_traffic_does_not_erase_the_weighted_share` | B2: a tenant weighted 4 takes exactly 80 of 100 inference dispatches, with and without one preparation dispatch per inference dispatch |
| `test_q1_kind__preparation_traffic_does_not_erase_a_service_time_debt` | B2: with 11 s against 1 s costs, the expensive tenant's share of service *seconds* stays within 0.05 of 0.50 under preparation traffic (0.92 with a shared virtual time) |
| `test_q1_fair__ties_break_on_arrival_not_on_flow_creation_or_on_the_org_id` | B3: the tie-break is the candidate's arrival sequence; flow creation order and the org id both point elsewhere in this case |
| `test_q1_fair__one_dispatch_moves_the_tag_by_exactly_cost_over_weight` | B3: 7 s of injected cost at weight 2 moves the tag by exactly 3.5, and the pool's virtual time to the dispatch's start |
| `test_q1_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing` | B4: `0`/negative/NaN/infinity/raising estimator → typed `internal_error`, `stats()` and `tags()` unchanged, candidate still pending and dispatched on the next claim |
| `test_q1_rebuild__a_duplicate_in_the_snapshot_is_indexed_and_charged_once` | B3/R21: a duplicate-bearing snapshot indexes once, charges the compact bytes of the distinct events once, and hands each out once |
| `test_q1_kind__visibility_expires_at_the_ttl_not_after_it` | the `>=` boundary: nothing at TTL − 1 µs, back at exactly the TTL |
| `test_q1_caps__a_full_index_refuses_with_a_typed_retryable_error` (extended) | a refused enqueue leaves `stats()` byte-identical and creates no flow |
| `tests/q/test_mutants.py` (gated) | 36 mutants declared, 3 + 6 self-tests by default, all 36 under `INFRX_MUTANTS=all`; each anchor still must appear exactly once |
| `test_the_runner_cannot_report_a_false_kill[a_named_case_that_does_not_notice_is_a_failure]` | r2: a mutant may not claim coverage from a case that cannot see it - `run_mutant` now reports `misdeclared` unless **every** named case fails. It caught one in this list on its first run (below) |

## Limits (round 1 additions and changes)

- **The per-kind virtual time is now part of the contract Q2 must reproduce**: one float
  per dispatch kind, `kind=None` comparing `(tag − V[kind], seq)`. A Valkey port needs a
  key per kind (`q:{kind}:v`) and the same comparison, or the two adapters will disagree
  on an unfiltered claim. Named here because it is the first thing Q2's differential run
  should assert.
- **Float tags drift** (10^6 unit dispatches reach 111110.99999952753). Deliberately not
  fixed: Valkey ZSET scores are doubles, so exact rationals in this adapter would create
  a divergence Q2 could not reproduce. Q2 must perform the same operations in the same
  order; the differential run is where that is proved.
- **The ordering fix of B4 has no mutant of its own.** The validation it added does
  (`a_bad_service_cost_is_not_validated`), and the case asserts the index is unchanged,
  but "compute before you mutate" cannot be expressed as a single edit that dies on an
  assertion or a typed error rather than on a `NameError`, which the list's own
  convention excludes. Stated rather than implied.
- **Round 0's three uncoverable invariants stand** (no cap on rebuild; claiming mutates
  no durable state; determinism across identical input). The determinism property gained
  nothing in round 1: it still cannot be killed by a deterministic single edit, and the
  reviewer's `PYTHONHASHSEED` variation is the check that can.
- **The acknowledged-id set costs about 119 bytes per id** (a 36-character UUID string in
  a CPython set) until a `rebuild` clears it: ~1.2 MB per 10,000 acknowledged candidates.
  Bounded in practice by Q3's reconciler, which owns pruning it against terminal jobs.
- **The item cap is global, not per organization.** One tenant can fill 500 index slots
  and make every other tenant's enqueue `capacity_exhausted`. Fairness applies to
  *dispatch*, not to *admission into the index*, and admission capacity belongs to
  PostgreSQL (`MAX_ACTIVE_JOBS_PER_ORG`), which is why this is a note and not a fix: a
  per-org index cap would need a contract decision about what a refused index write means
  for a job PostgreSQL has already admitted. Owner: Q3 with the coordinator.
- **Default `make api-test` no longer runs the whole Q mutation list** (3 of 36). A
  regression in an unselected mutant is caught by `make api-mutants` (once the Makefile
  line below lands), by `INFRX_MUTANTS=all`, or not at all until then — the honest cost
  of decision 1.

## Integration requests (round 1)

1. **Unchanged, accepted:** `max_index_items: int = 500` / `max_index_bytes: int =
   268_435_456` in `PilotSettings` (`MAX_INDEX_ITEMS`, `MAX_INDEX_BYTES`), plus the `08`
   §5 rows.
2. **Unchanged:** the composition-root construction when a scheduler is first needed
   (`MemoryScheduler(lambda: datetime.now(timezone.utc), limits=settings)` while
   `VALKEY_URL` is unset). No router.
3. **New, per decision 1:** add `tests/q/test_mutants.py` to the `api-mutants` target so
   the gated list runs there, e.g.
   `cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/q/test_mutants.py`.
   Q has already gated its list behind the same `INFRX_MUTANTS` variable, so the target
   picks up all 36 mutants with no further change on Q's side.
4. **Noted, no action for Q1:** when the coordinator exports a parameterised mutation
   runner from `tests/contracts`, Q switches `tests/q/mutants.py` to it and deletes its
   own `run_mutant` (decision 2).

## Verification log

- 2026-09-21: Authored from the commands above at implementation SHA `4e48eee`. Every
  count and summary line is quoted from captured output; no number in this report was
  typed by hand from memory. Nothing is claimed as integrated or live-verified.
- 2026-09-21: Review round 1 appended at `e56223f`. B1–B5 fixed, each with a case that
  fails without its fix and a declared mutant; the two false claims of round 0 are marked
  `CORRECTED` in place rather than removed. B1 and B2 were re-measured here (109 and 239
  of 300 seeded workloads change order) rather than restated from the review. Mutation
  list gated per decision 1; 36/36 killed. Timings carry a load caveat (`load average:
  36.17`) and the gating effect is stated as subprocess counts instead.
- 2026-09-21: Round-1 counts refreshed at `5a5602a` after a late strengthening of the
  runner: it now reports `misdeclared` unless every case a mutant names fails. The first
  run of that rule found a mutant of mine claiming coverage it did not have -
  `virtual_time_is_shared_across_dispatch_kinds` named the service-time case, which passed
  because a 1 s preparation cost makes the shared scalar advance at exactly the cheap
  tenant's rate and hides the defect. The fixture now prices the preparation tenant at 11 s
  too, both named cases fail under the mutant, and a self-test pins the new outcome. Only
  round-1 numbers were updated; the round-0 body still says what it said, corrections
  marked in place.

---

# Review round 2 — coordinator review of `e56223f`/`309fb1c`: `fix_required` (B6, B7)

Appended. B1, B3, B4 and B5 were confirmed fixed; B2 was fixed for kind-**filtered**
pools and regressed kind-**unfiltered** ones, which is B6, and its new comparison turned
out to be exercised by no test at all, which is B7. Both false claims of round 1 are
marked `CORRECTED` where they were made.

| Field | Value |
|---|---|
| Reviewed SHA | `309fb1c` |
| Round-2 implementation SHA | `f7a4743` (`b762c91` R60, `a26b28e` N03/N18/B4, `2fb2a1f` docstring, `5b0217e` anchors + level-1 comparison mutant, `f7a4743` the reviewer's third ordering mutant) |
| Status | **implemented, not integrated** |
| Suite | 59 tests in `tests/q`, 51 mutants, 51/51 killed |

## What R60 changes

Ruling R60 (binding, `08` §10 on the integration branch) replaces the withdrawn
`tag − V[kind]` comparison with **two-level selection**:

* **Level 1 — the kinds are flows.** Each dispatch kind has its own tag, they share one
  top-level virtual time, their weight is 1, and **only an unfiltered claim reads or
  writes this state**. Among kinds with an eligible candidate the smallest
  `(kind tag, arrival sequence of the candidate that kind would hand out)` wins, then
  `start = max(kind tag, V_top)`, `V_top = start`, `kind tag = start + cost`.
* **Level 2 — unchanged.** Inside the chosen kind, the per-tenant rule exactly as it was,
  and its key is back to `(tag, arrival sequence)` because within one kind the virtual
  time is a constant.
* **A rebuild clears level 1** with everything else.

Why the r2 comparison could not work, in one line: a kind's virtual time only advances
when that kind is dispatched, so a flow in the kind nobody is dispatching keeps its lag
for ever. Measured on the r2 head by the reviewer: a peer that enqueued three preparation
candidates behind N inference candidates was served at slots `[1, N+1, N+2]` for
N = 30/1,000/10,000 (round-1 head: `[1, 3, 5]`); a peer of weight 1 in the other kind got
one dispatch in 200,000; one org holding both kinds produced `aI aP bP` and then
37 inference candidates in a row.

## Disposition

| Item | Commit | Killing case / mutant |
|---|---|---|
| **B6** unfiltered claims starve across kinds; R60 | `b762c91` | `test_q1_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog` (peer slots `[1, 3, 5]` at N = 5/30/400 **and** at noisy weight 2) — mutants `an_unfiltered_claim_ignores_the_kind_level`, `the_kind_tag_does_not_advance`, `the_kind_choice_takes_the_largest_kind_tag` |
| **B6** one org with both kinds vs another org's single-kind work | `b762c91` | `test_q1_none__one_org_with_both_kinds_cannot_starve_another_orgs_single_kind_work` (kinds alternate `[INFER, PREPARE] × 4`; inside preparation the orgs alternate `A B A B`) — mutants `the_kind_tag_does_not_advance`, `an_unfiltered_claim_ignores_the_kind_level` |
| **B6** mixed pools; level 1 is untouched by filtered claims | `b762c91` | `test_q1_none__a_filtered_worker_never_moves_the_kind_state` (asserts `kind_tags()`/`top_virtual_time()` unmoved by a filtered claim, then the unfiltered worker alternating) — mutant `a_filtered_claim_moves_the_kind_state` |
| **B6** a new flow arrives at its own kind's virtual time | `b762c91` | `test_q1_none__a_new_flow_arrives_at_its_own_kinds_virtual_time` (preparation pool 50 units ahead; the newcomer still interleaves `B A B A`) — mutant `a_new_flow_arrives_at_the_highest_virtual_time` (the N05 survivor) |
| **B6** level-1 catch-up clamp | `b762c91` | `test_q1_none__a_kind_that_waited_catches_up_once_and_cannot_hoard` (`[PREPARE, INFER, PREPARE, INFER]` after the delayed kind becomes available) — mutants `the_kind_start_is_not_clamped_to_the_top_virtual_time`, `the_top_virtual_time_never_advances` |
| **B6** rebuild clears level 1 | `b762c91` | `test_q1_rebuild__clears_the_kind_level_state` — mutant `rebuild_keeps_the_kind_level_state` |
| **B7** the cross-kind comparison was vacuously proven | `b762c91`, `5b0217e` | The level-1 key now has its own mutants: `the_kind_tie_breaks_on_dict_order` and `the_kind_tie_breaks_on_the_kind_name`, both killed by `test_q1_none__the_kind_tie_breaks_on_arrival_order`, whose two halves make dict order wrong in one and the kind name wrong in the other; `an_unfiltered_claim_ignores_the_kind_level` is the N12 "raw tag across kinds" survivor, now killed by three cases |
| **N03** V set to the unclamped tag | `a26b28e` | `test_q1_fair__a_candidate_that_waited_catches_up_once_and_cannot_hoard` now asserts `virtual_times()` after the delayed flow is dispatched (4.0, not 0.0) and the exact order after it — mutant `the_virtual_time_is_the_unclamped_tag` |
| **N18** V written before the cost is validated | `a26b28e` | new `test_q1_fair__a_bad_service_cost_after_a_dispatch_moves_no_virtual_time` (one successful dispatch first, so the tag exceeds the virtual time; both bad-cost cases now compare stats, tags, virtual times, kind tags and the top-level virtual time) — mutant `the_virtual_time_is_written_before_the_cost_is_validated` |
| **B4 limit was wrong** | `a26b28e` | Both ordering mutants exist and are killed: `the_claim_is_recorded_before_the_cost_is_validated` (dies on the unchanged-state assertion) and `the_candidate_leaves_its_flow_before_the_cost_is_validated` (the literal r2 bug; dies on the candidate never being re-offered). The round-1 limit paragraph is corrected below |
| Empty-flow re-arrival | `2fb2a1f` | Documented in `_forget`'s docstring and in §Limits below; no behaviour change |
| Evidence corrections | `5b0217e` | "every finding addressed" and "comparable across kinds" marked `CORRECTED` in place |

### Correction to the round-1 limit on B4

Round 1 said: *"The ordering fix of B4 has no mutant of its own … 'compute before you
mutate' cannot be expressed as a single edit that dies on an assertion or a typed error
rather than on a `NameError`."* **That is wrong.** Two single-edit mutants express it and
both die on assertions in the named case: inserting `entry.claimed_at = now` before the
cost call, and inserting `flow.events.remove(event_id)` before it. The reviewer found them
with this repository's own runner. They are in the list; the paragraph stands above as
history with this correction attached.

## Commands and results (round 2, UTC 2026-09-21, `load average: 13.93` at the start)

| # | Command | Exit | Output |
|---|---|---|---|
| 1 | `make api-test` | 0 | `729 passed, 2 warnings in 33.71s` (670 baseline + 59) |
| 2 | `uv run --frozen pytest -q tests/q` | 0 | `59 passed in 6.00s` |
| 3 | `uv run --frozen pytest tests/q/test_memory_scheduler.py -k contract -q -s` | 0 | `scheduler conformance: 7 ran, 0 skipped []` then `9 passed, 36 deselected in 0.16s` |
| 4 | `uv run --frozen pytest -q -s tests/q/test_mutants.py` | 0 | `Q mutants: 51 declared, 3 selected (default subset)` then `10 passed in 4.92s` |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q -s tests/q/test_mutants.py` | 0 | `Q mutants: 51 declared, 51 selected (INFRX_MUTANTS=all)` then `58 passed in 41.92s` |
| 6 | `uv run --frozen python tests/q/mutants.py` | 0 | `51/51 killed` |
| 7 | `PYTHONHASHSEED=0/1/999 pytest -q tests/q/test_fairness_properties.py tests/q/test_memory_scheduler.py` | 0 | `49 passed` on each seed |

Counts: 59 tests in `tests/q` = 9 contract (7 exported cases, 0 skips) + 4 property +
36 behaviour/drill (7 of them new for R60) + 10 mutation (1 well-formedness + 3 subset
mutants + 6 runner self-tests). `make api-test` = 670 baseline + 59. No failures, no
skips in any command. `make console-*` and `make bench-test` remain not run (nothing
console or bench is touched); real-service evidence is still Q2's.

Two failures happened during the round and are recorded rather than hidden: the
exactly-once anchor rule failed on its own account after R60 (`if best is None or
order < best[0]:` now exists at both selection levels, and rebuild's reset block grew two
lines), and one expectation in the N03 change was wrong on first writing (the noisy tenant
has one candidate left at that point, so the order is `A B B B`). Both are fixed in
`5b0217e` and `a26b28e`; the anchor rule catching a real ambiguity is the reason it exists.

## Validation against the reviewer's own attack scripts (round 2)

The review named its repro scripts, so the fix was checked against **them**, not only
against the cases written from their description. The scripts were copied into this
session's scratch area with their `API` path repointed at this worktree (one line in
`lib.py`); nothing else was changed, and nothing in the worktree was touched. Quoted
output at `f7a4743`:

```
$ python none_starve.py
equal weights, noisy depth    30: peer in SAME kind dispatched at slots [1, 3, 5]; peer in OTHER kind at slots [1, 3, 5]
equal weights, noisy depth  1000: peer in SAME kind dispatched at slots [1, 3, 5]; peer in OTHER kind at slots [1, 3, 5]
equal weights, noisy depth 10000: peer in SAME kind dispatched at slots [1, 3, 5]; peer in OTHER kind at slots [1, 3, 5]
noisy weight 2, peer weight 1, depth 10000, other kind: [1, 3, 5]
  same, same kind: [1, 4, 7]

$ python none_more.py
same org two kinds, kind=None: aI aP aI bP aI aP aI bP aI aP aI bP aI aP aI bP aI aP aI bP aI aI aI aI …
noisy weight 2 topped up for 200,000 unfiltered dispatches; peer (weight 1, other kind) dispatched at slots: [1, 3, 5] | peer lag [0.5]

$ python mixed.py
mixed filtered+unfiltered workers: prepare peer dispatched at slots [2, 6, 10]

$ python kinds.py
prep dispatches per inference dispatch=0: B share=0.80 (weights say 0.80)  ABBBBABBBBABBBB…
prep dispatches per inference dispatch=1: B share=0.80 (weights say 0.80)  ABBBBABBBBABBBB…
prep dispatches per inference dispatch=3: B share=0.80 (weights say 0.80)  ABBBBABBBBABBBB…

$ python kinds_cost.py
prep dispatches per inference dispatch=0:  expensive tenant's share of GPU service seconds = 0.50 (fair = 0.50)
prep dispatches per inference dispatch=12: expensive tenant's share of GPU service seconds = 0.50 (fair = 0.50)

$ python survivor_demo.py
N05 real: BABABAAAAAAA | arrival at max(V of any kind): AAAAAAAAAAAA
N03 real: (4.0, 'CACAA') | V := unclamped tag: (0.0, 'CCAAA')

$ python b4.py
typed: internal_error status 500
state identical incl. virtual_times and flow order: True
next claim dispatches: 00000002

$ PYTHONPATH=<worktree>/apps/infrx-api python b4_mutant.py
the_candidate_leaves_its_flow_before_the_cost_is_validated (statement move = the round-1 bug) -> Result(outcome=<Outcome.killed: 'killed'>, detail='1 failed, 58 deselected in 0.38s')
the_claim_is_stamped_before_the_cost_is_validated (pure one-line insert) -> Result(outcome=<Outcome.killed: 'killed'>, detail='1 failed, 58 deselected in 0.37s')
the_cost_is_validated_after_the_state_moves (validation call moved below the writes) -> Result(outcome=<Outcome.killed: 'killed'>, detail='1 failed, 58 deselected in 0.36s')

$ for seed in 0 1 7; do PYTHONHASHSEED=$seed python invariants.py; done
hashseed 0 digest 72975340aa7487bb refusals 33830 dispatches 29531
hashseed 1 digest 72975340aa7487bb refusals 33830 dispatches 29531
hashseed 7 digest 72975340aa7487bb refusals 33830 dispatches 29531

$ python test_index_only.py        # silent: every assertion held
```

Three notes, because two scripts did not simply pass:

- `b4_mutant.py`'s third mutant (`cost = 1.0`, the estimator never consulted) was **not**
  in this list. It is now (`the_estimator_is_never_consulted`, `f7a4743`), named against
  the bad-cost case and the cost-over-weight case, and both fail under it.
- `tiebreak.py` aborts on `assert src.count(old) == 1` for the anchor
  `order = (flow.tag - self._virtual_time[flow_kind], …)`: that expression is what R60
  withdrew, so the script is probing a line that no longer exists. The invariant it was
  probing is covered by `the_kind_tie_breaks_on_dict_order` /
  `the_kind_tie_breaks_on_the_kind_name` at level 1 and by the three level-2 tie-break
  mutants.
- `b4_move.py` reads the adapter from the reviewer's own tree path, which does not exist
  here; `b4_mutant.py` covers the same two mutants and was run.

The `invariants.py` digest being identical on three hash seeds is the
`PYTHONHASHSEED` check round 1 could only promise: 29,531 dispatches and 33,830 refusals
of a randomised operation stream, same digest each time.

## Artifacts (round 2)

| File | sha256 |
|---|---|
| `q1/r3-api-test.txt` | `c02f981e79144dd02716ca49446708e68d7f2a356acc6885459b7fd9461a7434` |
| `q1/r3-q-suite.txt` | `cbfe7bc4c16a66bfa7c169785966cc24e5e30de87fcf0d404ab45450991d1167` |
| `q1/r3-conformance.txt` | `a558120edb89de2735b917417c1ad03e9131f435ba602376526fdb61cfea1cb3` |
| `q1/r3-mutants-subset.txt` | `c0fe825063c3ddf963abd3c3cc7f211a24f98dec97604fd042500f1233d2c4b7` |
| `q1/r3-mutants-all.txt` | `949b149ac3779af538ed638b8f3b074e9f2f3f6d408577e9688bc72879b0ad72` |
| `q1/r3-mutants.txt` | `56029e15ba1f7b0bb695b4a6814f58f83cc28f70c3ade92590885afaa7ab7788` |

## Limits (round 2)

- **Level-1 state is not deleted when a kind empties.** Both kind tags live for the
  index's lifetime and are cleared only by `rebuild`. A kind that goes quiet and comes
  back is bounded to one catch-up slot by the `max(kind tag, V_top)` clamp (the case
  above), so the SFQ arrival rule buys nothing extra here and there is no third level of
  state to keep in sync. If a third dispatch kind is ever added, this is the decision to
  revisit first.
- **Empty flows are still deleted** when a tenant's last candidate leaves, which is what
  keeps cancelled and idle tenants from holding stale fairness state (Q1 acceptance) and
  means a tenant that keeps at most **one** candidate indexed re-arrives at lag 0 every
  time. The advantage is one slot per empty-to-backlogged transition, bounded by the
  tenant's own concurrency, so it starves nobody; it is documented in `_forget` because Q3
  owns the estimator that decides what a slot is worth.
- **An unfiltered claim charges the same cost twice**, once at each level (weight 1 at
  level 1, the tenant's weight at level 2). That is R60 as written and it is what makes
  the kinds share capacity in proportion to service time rather than request count; it
  also means level-1 tags are not comparable with level-2 tags, which only matters to
  anybody reading `kind_tags()` next to `tags()`.
- Everything from rounds 0 and 1 that was not superseded still stands: no real-service
  evidence (Q2), provisional constant estimator, no per-org weight source, no priority
  bands, float tags drift by design, the global item cap, the acknowledged-id set, and the
  three invariants that cannot carry a mutant (no cap on rebuild, claiming mutates nothing
  durable, determinism under identical input — `PYTHONHASHSEED` 0/1/999 all pass).

## What Q2 must reproduce (the reviewer's corrected list, round 3)

This replaces the twelve points written in round 2: the round-3 reviewer built an
independent reference model of R60 from the ruling and found the list incomplete in the
places its own model needed spelling out. The memory adapter is the reference for every
item, **not** the fake.

1. **One arrival-sequence counter across both kinds**, integer, and it restarts at 1 on
   `rebuild`.
2. FIFO inside a flow by that sequence. A candidate whose visibility timed out re-enters
   at its **original** sequence, and a not-yet-available **older** candidate does not
   block a newer available one in the same flow.
3. An arriving flow's tag is **its own kind's** virtual time; `V[kind]` is **never** reset
   when a kind or a flow empties (only `rebuild` resets it).
4. Level 2, in this float order: `start = max(tag, V[kind])`; `V[kind] = start`;
   `tag = start + cost / weight`.
5. Level 1 (R60), for unfiltered claims only: the kinds are flows with their own tag, one
   shared top-level virtual time and weight 1. A kind is ranked by
   `(kind tag, arrival sequence of the candidate **its own level-2 rule would hand
   out**)`; a kind with no eligible pick is skipped and left untouched. The writes are
   `top_start = max(kind tag, V_top)` → `V_top = top_start` → `kind tag = top_start +
   cost` (no weight), and they happen **before** the level-2 writes.
6. A kind-**filtered** claim never reads or writes level-1 state.
7. A flow is deleted when its pending **and** in-flight count reaches zero.
8. The service cost is validated **before any write**; a bad answer (`0`, negative, NaN,
   infinity, or an estimator that raises) is `internal_error` with the index
   byte-identical afterwards, and an estimator's own `DomainError` passes through
   **unchanged**.
9. Visibility: 30 s preparation, 120 s inference, timed **from the claim**, back when
   `now >= claimed_at + TTL`. Expiry is evaluated **lazily**, at the start of
   `claim_candidate`, after kind validation, and across **both** kinds - so
   `stats()["inflight"]` can still count entries whose visibility has expired until the
   next claim.
10. `enqueue` checks in this order: duplicate (pending, in flight or acknowledged → `False`,
    no write) → item cap → byte cap, both caps before any write, refusal
    `capacity_exhausted` with `retry_after_s = 1`.
11. `remove(job_id)` drops every candidate of that job, pending or in flight, and is
    **not** remembered - a replayed dispatch event may be re-indexed.
12. `rebuild`: pending = the de-duplicated snapshot in snapshot order, caps **not**
    applied, in-flight dropped, acknowledged cleared, **both** fairness levels reset, the
    sequence counter restarted, bytes = the sum of the snapshot's compact bytes.
13. An unknown kind is `invalid_request` (R55), checked **before anything else**;
    `kind=None` means "anything".
14. The differential run compares `tags()`, `virtual_times()`, `kind_tags()`,
    `top_virtual_time()` and `stats()` after **every** operation, including unfiltered
    claims with both kinds holding work **and non-unit costs** - the two cases the r2 and
    r3 heads had no test for.

## Verification log

- 2026-09-21: Review round 2 appended at `5b0217e`. R60 implemented as ruled; seven new
  cases and eleven new mutants for unfiltered claims, three more for N03/N18 and the two
  B4 ordering mutants the round-1 limit wrongly said could not exist. 50 mutants declared,
  50 killed, every named case failing under its mutant. The round-1 claims "every finding
  addressed" and "comparable across kinds" are marked `CORRECTED` in place; the round-1
  B4 limit is corrected in this section. Counts quoted from the commands above.

---

# Review round 3 — coordinator review of `1d721e6`: `fix_required` on tests only

Appended. Round 3 built an **independent reference model of R60** and reported that this
adapter matched it exactly — tags, per-kind virtual times, kind tags, the top-level
virtual time, bytes, items and in-flight counts — over 40 seeds × 2,500 operations. **No
implementation defect, and the adapter was not changed in this round.** Two R60 clauses
had surviving mutants because no test exercised them, which is the whole of B8 and B9.

| Field | Value |
|---|---|
| Reviewed SHA | `1d721e6` |
| Round-3 implementation SHA | `cae5142` (tests only) |
| Status | **implemented, not integrated** |
| Suite | 61 tests in `tests/q`, 54 mutants, 54/54 killed |

## Disposition

| Item | Commit | Case → mutant |
|---|---|---|
| **B8** (T07) `kind tag = start + cost` unproven: `+ 1.0` passed the whole suite because no test issued an unfiltered claim with an injected estimator | `cae5142` | `test_q1_none__the_kind_tag_advances_by_the_service_the_kind_received` — preparation 11 s against inference 1 s, both kinds backlogged, 26 unfiltered claims, asserting **both** the pattern `IPIIIIIIIIIIIPIIIIIIIIIIIP` and `kind_tags() == {prepare: 33.0, inference: 23.0}` → mutant `the_kind_tag_advances_by_one_not_by_the_cost` |
| **B9** (T22) the level-1 tie-break "sequence of the candidate that kind would hand out" unproven: every level-1 case had one flow per kind, so ranking by the kind's **oldest** candidate survived | `cae5142` | `test_q1_none__the_kind_is_ranked_by_the_candidate_it_would_actually_hand_out` — `ORG_A` holds preparation's sequences 1 and 2 and has been served once by a preparation-**filtered** worker (tag 1.0, level-1 state untouched), so preparation's pick is `ORG_B` at sequence 4 while inference holds sequence 3; the unfiltered claim must be the inference candidate → mutant `the_kind_is_ranked_by_its_oldest_candidate` |
| **T04** the top-level virtual time was not asserted | `cae5142` | `test_q1_none__a_kind_that_waited_catches_up_once_and_cannot_hoard` now claims the late dispatch on its own and asserts `top_virtual_time() == 3.0` before continuing → mutant `the_top_virtual_time_is_the_unclamped_kind_tag`. Order-equivalent with two kinds, but it is state Q2's differential run compares, so it is pinned rather than argued about |
| Q2 reproduction list | `cae5142` | Replaced with the reviewer's corrected list (§ above): one sequence counter across both kinds restarting at 1 on rebuild, availability not blocking inside a flow, `V[kind]` never reset on empty, a kind with no pick skipped untouched, the level-1 write order, an estimator's `DomainError` passing through, lazy visibility expiry after kind validation, the `enqueue` check order with `retry_after_s = 1`, unknown kind checked first, and non-unit costs in the differential run |
| Verification log gap | `cae5142` | The round-2 log line said "50 mutants declared" at `5b0217e` while the table already said 51 after `f7a4743`; the log below now covers `f7a4743`, `1d721e6` and `cae5142` |

Two corrections the coordinator made to **R60's own text** on the integration branch, noted
here so this report and the ruling agree: the tie-break clause now says "the candidate that
kind would hand out" explicitly, and the starvation bound is *kinds × tenants-in-its-kind at
equal cost, measured in service time*. Both were errors in the ruling's wording; no claim in
this report depended on either, and the bound this suite asserts (`[1, 3, 5]`, i.e. one slot
per kind) is the equal-cost case of the corrected one.

## Commands and results (round 3, UTC 2026-09-21, `load average: 10.00`)

| # | Command | Exit | Output |
|---|---|---|---|
| 1 | `make api-test` | 0 | `731 passed, 2 warnings in 31.21s` (670 baseline + 61) |
| 2 | `uv run --frozen pytest -q tests/q` | 0 | `61 passed in 5.26s` |
| 3 | `uv run --frozen pytest tests/q/test_memory_scheduler.py -k contract -q -s` | 0 | `scheduler conformance: 7 ran, 0 skipped []` then `9 passed, 38 deselected in 0.16s` |
| 4 | `uv run --frozen pytest -q -s tests/q/test_mutants.py` | 0 | `10 passed in 7.60s` (3-mutant default subset) |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q -s tests/q/test_mutants.py` | 0 | `61 passed in 49.57s` (1 well-formedness + 54 mutants + 6 self-tests) |
| 6 | `uv run --frozen python tests/q/mutants.py` | 0 | `54/54 killed` |

Counts: 61 tests in `tests/q` = 9 contract (7 exported cases, 0 skips) + 4 property +
38 behaviour/drill + 10 mutation. No failures, no skips. Console and bench targets remain
not run; real-service evidence remains Q2's.

## Artifacts (round 3)

| File | sha256 |
|---|---|
| `q1/r4-api-test.txt` | `d1d11b6108a28410bd58d04dc75a82d4ab462dcae96dc8afc3a38e7706673ae4` |
| `q1/r4-q-suite.txt` | `baf882fe06c82e446502c863078b76227d23b4b7a6f7f805194c2d13c8a7e8ac` |
| `q1/r4-conformance.txt` | `2c19ab2c43def0b695ac0b93c10626035186aebc6df40c822934bb710dd5e9c9` |
| `q1/r4-mutants-subset.txt` | `df418c1070c2a27b7dedb95b608cd8de066754156f904b160ae4db9e6c3a4c59` |
| `q1/r4-mutants-all.txt` | `5ebdf14dbdfa0eb222c3099db424713098911ff84f152bc453f0913f9bf116b7` |
| `q1/r4-mutants.txt` | `2d58c8740fe657f40cba743acd84902c02d77568ba1a5cd357b288598112c027` |

## Limits (round 3 addition)

- **Level-1 debt is in service time, and it is inherited across the tenants of a kind.**
  A kind's tag advances by the cost of whatever was dispatched from it, whichever tenant
  that was, so an expensive tenant in the preparation pool makes the *whole* preparation
  pool wait longer for its next unfiltered slot — that is what makes the two pools share
  the machine by service time rather than by request count (B8's case measures it: one
  preparation slot per eleven inference slots at 11:1 costs), and it is also the reason a
  cheap tenant in an expensive pool can be slower to reach an unfiltered worker than it
  would be with its own pool. Level 2 keeps that tenant's standing *inside* its pool
  exact. Whoever sets the estimator in Q3 owns this trade-off; it is a property of R60,
  not a defect, and no test asserts a bound on it.
- Everything unsuperseded from rounds 0–2 stands.

## Verification log

- 2026-09-21: Round-2 follow-ups at `f7a4743` and `1d721e6`, which the round-2 log line
  above predates: `f7a4743` adopted the reviewer's third ordering mutant
  (`the_estimator_is_never_consulted`, `cost = 1.0`), taking the list from 50 to
  **51 declared, 51 killed**, and `1d721e6` appended the validation of this adapter against
  the reviewer's own attack scripts. The round-2 table's counts are the ones that hold.
- 2026-09-21: Review round 3 appended at `cae5142`, tests only — round 3 found **no
  implementation defect** (an independent reference model of R60 matched this adapter over
  40 seeds × 2,500 operations), so the adapter is byte-identical to `1d721e6`. B8, B9 and
  T04 now have cases and mutants: 54 declared, 54 killed, every named case failing under
  its mutant. The Q2 reproduction list is replaced by the reviewer's corrected version and
  the level-1 service-time trade-off is recorded under Limits. Counts quoted from the
  commands above.

## Coordinator correction (2026-09-21, stage review S2)

The "Limits, round 1" section above still states the round-2 rule for an unfiltered claim (`kind=None` comparing `(tag − V[kind], seq)`). That rule was **withdrawn** by ruling R60 and replaced by the two-level selection described in the round-3 sections and in §"What Q2 must reproduce"; the earlier text is kept as history and is CORRECTED by this note.
