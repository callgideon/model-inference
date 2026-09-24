# F2C-L slices b and d, plus the slice-a verifier findings — evidence

Lane F2C-L. Slice a is recorded in `F2C-lifecycle-2d5e474.md` (append-only; its ruling numbers are superseded here by names — the coordinator numbers them at merge).

- Base `dff31efc`. Commits: `10c03d82` (slice b), `c058835a` (tracker update), `3a21e0bf` (verifier findings F1–F10), `9bf7a95f` (slice d). Evidence head: the commit adding this file.
- Scope: contracts, their fakes, suites and fixtures, contract docs. No SQL, route, adapter or composition change.

## Changed paths (since slice a)

| Path | What |
|---|---|
| `apps/infrx-api/infrx/contracts/records.py` | `TerminalOutcome.result_expires_at` (optional; only a success with a result, after `settled_at`) |
| `apps/infrx-api/infrx/contracts/fakes/state.py` | settlement persists `result_expires_at` once from the store's TTL |
| `apps/infrx-api/infrx/contracts/v2/lifecycle.py` | `ReadOutcome` + `read_outcome`, `result_case_table`; scrub and per-kind retention rules; `media_refused`, `UPLOAD_ABORT_REASONS`, `CONSTRAINT_NAMES`; strict `duration_s`; docstrings (F9) |
| `apps/infrx-api/infrx/contracts/fakes/lifecycle.py` | result content kept to the persisted expiry; typed digest check; abort limited to public reasons |
| `apps/infrx-api/infrx/contracts/fakes/factories.py` | legacy USD seeding, `balance`/`retune` hooks, `set_capability(..., stream_output)`, window keywords |
| `apps/infrx-api/infrx/contracts/conformance/lifecycle.py` | 25 cases (was 19): both regimes, RESULT-EXPIRY ×4, abort/too_large, delete reconciliation, F4–F7 guards |
| `apps/infrx-api/infrx/contracts/conformance/acceptance.py` (new), `fixtures/acceptance/lifecycle.json` (new) | versioned transcripts `f2c-lifecycle-acceptance.1`; `replay(factory)` |
| `apps/infrx-api/infrx/contracts/fixtures/v1/terminal_success_expiring.json` (new), `fixtures/__init__.py`, `fixtures/v2/result_read_cases.json` (new), `v2/fixtures.py`, `fixtures/v2/map.json`, `lifecycle_refusals.json` | committed success with expiry beside the old record; the cross-language read table |
| `apps/infrx-api/infrx/contracts/README.md` | F2C module rows |
| `apps/infrx-api/tests/contracts/v2/test_lifecycle.py`, `test_fixtures.py`, `mutants.py` | record/table/acceptance tests; the fixture-root guard admits `acceptance/`; 36 new mutants (70 F2C total) |
| `apps/app/lib/contracts/v2/lifecycle.ts`, `apps/app/tests/contracts/v2/lifecycle.test.ts`, `tests/contracts/mutants.json` | `READ_OUTCOMES`, `decodeTerminalOutcome`, `readOutcome`, `UPLOAD_ABORT_REASONS`; 5 new console mutants |
| `research/plan/02-durable-protocols.md` | appended: terminal/read consistency, findings note, migration rollout contract |
| `research/plan/01-contracts.md` | appended: F2C.b summary, changed-field/consumer matrix with file:line |

## Commands (host Linux; final runs at `9bf7a95f`)

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/contracts -k "not pg"` | 0 | **1162 passed**, 6 deselected (the PG projection cases; the shared PG port 55432 is held by other lanes — `codex-e2c-verify`, later `m5-wired`) |
| `uv run --frozen pytest -q tests/contracts/v2/test_lifecycle.py` | 0 | 61 passed |
| `uv run --frozen python -m tests.contracts.mutants <70 lc_/lcb_/acc_ mutants>` | 0 | **70/70 killed** |
| `uv run --frozen pytest -q tests --ignore=tests/d --ignore=tests/contracts -k "not mutant and not pg"` | 0 | 1666 passed, 15 skipped |
| `uv run --frozen pytest -q tests --ignore=tests/d -k "not mutant and not pg"` (at `10c03d82`) | 0 | 2781 passed, 15 skipped |
| `uv run --frozen pytest -q tests/d -k "not mutant"` | 0 | 392 passed, 5 xfailed (PG-backed cases not selected; nothing touched the port) |
| `make console-test` | 0 | 297 passed |
| `node --test "tests/contracts/**/*.test.ts"` | 0 | 159 passed |
| `make console-lint` / `make console-typecheck` | 0 / 0 | 0 errors (2 warnings that were already there) / clean |
| `node tests/contracts/run-mutants.mjs --self-test` / `--entry v2` | 0 / 0 | 14/14; **41/41 killed** |

**F2C fixture hash** (sha256 over the sorted `sha256sum` lines of the 15 F2C fixture files: `fixtures/v2/lifecycle_*.json` ×12, `fixtures/v2/result_read_cases.json`, `fixtures/v1/terminal_success_expiring.json`, `fixtures/acceptance/lifecycle.json`): **`da29215d09b61a80030dc1b026aef0f3a389c4e712c9e36ad4acb303b9846380`**. The acceptance transcript alone: `824e960a66b36bb447ec5cff8fd613321b042f7c1d5364a06794a0f7c7653768`.

## Failed-then-passed

- Slice b: the RV-11 bug reintroduced (`lcb_read_recomputes_from_configuration`), an invented expiry (`lcb_read_invents_an_expiry`, console `V2-LC-08`), and the equality instant counted as available (`lcb_read_available_at_expiry`, `V2-LC-07`) are all killed.
- Verifier survivors are now killed: `lc_legacy_admission_writes_no_marker` (F1), `lc_abort_not_persisted`/`_not_idempotent`/`lc_too_large_unchecked`/`_untyped` (F2), `lc_tombstoned_never_offered_again`/`_never_reclaimed` (F3), and the F4–F8/F10 guards.
- Caught while working: a slice-a mutant anchor made stale by slice b (`lc_payload_unprotected_by_its_job`, misdeclared → re-anchored, killed). The console runner carries only `fixtures/v2`, so the TS tests now take v1 outcomes from the v2 table. The fixture-root guard refused the new `acceptance/` directory until it was admitted explicitly.
- Acceptance oracle: `replay` of a per-process store (the RV-02 behaviour) and of other windows under strict times both report mismatches; with `strict_times=False` the other windows are accepted.

## Verifier findings F1–F10 — all closed

| | Closure |
|---|---|
| F1 | Legacy USD seeded in `lifecycle_factory`. The admission cases run for `CARD` and `LEGACY`; "previous `admit`/`admit_credit` write no marker" runs for both. |
| F2 | New case `upload_restart__oversize_puts_and_aborts_are_final_everywhere`. |
| F3 | New case `retention_durable__an_unfinished_delete_is_reconciled_by_a_higher_fence`. |
| F4 | Added: claim inside the grace → `not_eligible`; claimed rows not offered; `MAX_PAGE` clamp (1001 rows); idempotent repeat ack; `expire(1)`. The tombstone-side `not_eligible` recheck is equivalent (eligibility cannot regress after a claim) and stays as defence. |
| F5 | Added: stream-output recheck; a non-source kind; an upload whose ticket names other content; a deleted row. |
| F6 | Added: another org's key → `not_found`; other bytes at a live key → `bytes_changed`. |
| F7 | Added: a clock move between put retries; size and mime compared on retries; a malformed digest is a typed `invalid_request`. |
| F8 | Tickets record only `UPLOAD_ABORT_REASONS` (new `media_refused`), in both languages. `parse` refuses `schema_version`. |
| F9 | The `ReadinessStore.admit_ready` docstring says `admit`/`admit_credit` never write a marker. |
| F10 | `FinalizedSource.duration_s` is a strict finite number. The inherited laxness (`schema_version` "2", epoch or `+00:00` instants on `RecordV2`/`Timestamp`) is an open item below. |

## Proposed rulings (by name; numbered at merge)

1. **Lifecycle ports and refusal table.** The three ports exist as encoded. Refusals are the closed set, each raised as an existing code. The reason rides on `DomainError.refusal`, is never serialized, and there is no new public code. One exception: an upload ticket records its abort reason, drawn only from `UPLOAD_ABORT_REASONS` (public facts about the caller's own bytes).
2. **One-phase execution readiness.** `admit_ready` checks everything below and records the manifest and marker in the admission transaction:
   - both regimes;
   - CREDIT only: the expectation card and the pinned revision's capability;
   - sources: the org's own live content with the same digest; an upload only through its finalized, unexpired ticket.

   A refusal admits nothing. An empty manifest is ready with zero sources; no marker means `not_ready`. Preparation refuses `not_ready`, and `admit`/`admit_credit` never write a marker.
3. **Readiness cutover without backfill.** Marker-less `preparing` jobs end at `preparation_deadline_at`, free. Each previous→new boundary drains preparing jobs. `admit_ready` dual-writes 0003 `job_media`.
4. **Content lifecycle.** Stable `content_id` per key. Generation increments on re-registration after the ack. Eligibility is persisted at first registration. The tombstone rechecks under the admission lock. A tombstoned key is `content_retiring` until the ack. An older generation's ack is a no-op. A tombstoned row whose claim lapsed is offered again and finished under a higher fence. The claim TTL must exceed the object-store request timeout.
5. **Lifecycle time authority.** All instants use database transaction time. Equality has passed. `retain_until` is set once at terminalization: for results it is the persisted `result_expires_at`.
6. **Persisted result expiry and the read classification (slice b).**
   - The settling transaction persists `TerminalOutcome.result_expires_at`; a proposal's value is ignored.
   - Absent means `unavailable` and is never recomputed.
   - `read_outcome` gives one of six outcomes on the store clock; scrubbing happens only after the persisted expiry.
   - Public wire bodies are unchanged.
   - It becomes required through a `NOT VALID` check, validated only at zero violations.

## Wiring requests

None. The consumer matrix (01 §F2C changed-field / consumer matrix) names each owner's change with file:line. The two composition points (`gateway/pilot.py:212`, `:280`) belong to the coordinator when D10/M5 land.

## Open issues

- Python `RecordV2`/`Timestamp` accept `schema_version` "2" and epoch or `+00:00` instants; TypeScript refuses them. This is inherited from v1/F2P. The remedy is a strict v2 base in one coordinated pass, because it touches F2P records too.
- The `SURFACE_VERSION` bump is deferred to the coordinator (after both F2C lanes merge).
- The PG-backed D and contract projection suites were not run: the shared port 55432 belonged to other lanes throughout.
- The D10 configured durations (upload window, grace, claim TTL, per-kind retention, result TTL) have no 08 §5 names yet. The retention values are P-25.
- Legacy admissions have no pinned serving revision; their capability stays an ingress check, as today.
- A strict replay of the acceptance transcript needs a factory that accepts `upload_ttl_s`, `grace_s`, `claim_ttl_s` and `retention_s`. Otherwise use `strict_times=False`.

## Remaining effort

F2C-L: 0 h of planned work (a, b, d delivered; review fixes only). Optimistic 0.5 h, likely 1 h, pessimistic 3 h (for a review round), confidence medium. Basis: every slice is committed and green, and the verifier's findings are closed.
