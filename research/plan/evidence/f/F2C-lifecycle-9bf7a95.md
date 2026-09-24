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

## Fix round (2026-09-24, slice-b/d verification findings)

This section is appended; nothing above it was edited. Code head: `16ce171a`, on top of `1b9c7411`. Evidence head: the commit that adds this section.

**Changed paths:** `conformance/lifecycle.py` (cases), `conformance/acceptance.py` (`VERSION` → `f2c-lifecycle-acceptance.2`), `fixtures/acceptance/lifecycle.json` (regenerated), `fixtures/v2/result_read_cases.json` (regenerated; 11 → 15 rows), `v2/lifecycle.py` (`result_case_table` rows; `ContentLifecycle` docstring), `tests/contracts/mutants.py` (+9), `apps/app/tests/contracts/mutants.json` (+3), `research/plan/01-contracts.md`, `research/plan/02-durable-protocols.md`. All of these are owned paths. No SQL, route, adapter or composition change. `fakes/` is not edited: every finding was a test gap or a spec gap. The fake's behaviour was correct.

### Findings

For each code finding, the mutant **survived** before the fix. That run is `python -m tests.contracts.mutants <9 new>` at `1b9c7411` plus the new mutant list: 1/9 killed, 8 survived. Every mutant is **killed** after the fix. `replay(lifecycle_factory)` also reports each case-level mutant with exactly one mismatch. The check applies each mutant to a scratch copy of `infrx/`, runs `acceptance.replay`, and looks for the step that diverges.

| id | Fix | Regression (fails before / passes after) |
|---|---|---|
| 0-M1 | `retention_durable__a_tombstone_refuses_new_use_until_the_delete_is_acked`: sweeper `s-slow` claims gen 1 and stalls until its claim lapses. `s-a` then tombstones and acks, and the key is re-registered as gen 2. `s-c` claims gen 2, and the fake gives it fence 1 again. `tombstone(stale)` must be `claim_lost`, and the key must stay live. 02 D4 now says the fence restarts per generation, so every claim check compares `(generation, fence)` together. | `lc_tombstone_ignores_the_generation` survived → killed; replay mismatch at step 17 |
| 2-ACI-1 | `retention_durable__claims_are_leased_and_fenced`: `tombstone(first)` is called while `second` holds the live claim, and must be `claim_lost`. | `lc_tombstone_ignores_a_newer_fence` survived → killed (replay step 12). The expiry conjunct alone is also guarded: `lc_tombstone_ignores_the_claim_expiry`, killed. |
| 0-M2 | `admission_ready__a_job_with_no_marker_is_never_claimable` now runs in the cutover state, for each regime. An `admit_ready` job of the same organization, with a marker, is created first. Then a previous-runtime job must read `readiness() is None` and `claim_preparation` must answer `not_ready`. | `lc_claim_gate_keyed_on_the_org` (S31b) and `lc_claim_gate_any_marker` (S31) both survived → killed; replay step 5 |
| 0-M3 | The three RESULT-EXPIRY cases that commit a success now run over `EXPECTATIONS`: `_succeeded(expectation=)` and `_owned_outcome`, which reads through `get_owned` for LEGACY and `get_owned_credit` for CARD. The configuration-change case promises both regimes' expiries before the retune. | `lcb_legacy_read_recomputes_from_configuration` (B9) survived → killed; replay step 16 |
| 2-ACI-2 | Four rows added to `result_read_cases.json`, all `no_result`: cancelled with a `result_ref`; failed with a `result_ref`; a success without a `result_ref`; a success released free without usage but with a persisted expiry. Python and TypeScript both classify every row. | Python `lcb_failure_with_a_ref_read_as_a_result`, `lcb_success_without_a_ref_read_as_a_result` and `lcb_unbilled_success_read_as_a_result`: survived → killed. Console `V2-LC-12/13/14`: against the old table "[the suite passed]" ×3; against the new table killed ×3. |
| 0-M4, 2-ACI-3 | 02 now has a "Scrub guard" paragraph, column by column. An UPDATE of `infrx.job_results` is allowed only if all of these hold: `old.scrubbed_at is null`, `new.scrubbed_at = infrx.now()`, `new.body = ''`, and `request_id`/`org_id`/`digest`/`bytes`/`created_at` are unchanged. The job must satisfy `j.settled_at is not null and (j.result_expires_at is null or infrx.now() >= j.result_expires_at)`. The `settled_at` term stops the scrub of an in-flight job's result, because `put_result` writes before settlement. DELETE and TRUNCATE stay forbidden. `job_results_bytes_exact` is replaced by `check (scrubbed_at is not null or bytes = octet_length(body))` + `check (scrubbed_at is null or body = '')`. `read_result` refuses a scrubbed row as `result_expired`. "Keeps no result" is defined: every non-success, and a success settled before 0018 with a NULL expiry. Expand names this as the one allowed drop-and-recreate. The `ContentLifecycle` docstring and the 01 matrix rows (`0014:24/:30/:68`) point to this text. | spec (checked against `0014_job_results.sql:15-36,68-80`, `0018:436`) |
| 0-M5 | The `jobs` expiry check is taken out of expand (step 1). Step 2 adds it, validated, only at zero violators, and never `NOT VALID` over a violating row. The reason is that later UPDATEs re-check the constraint, and settled successes are still updated (`0017:320` journal expiry; `0016:482`, `0018:513` aged-hold release), so one violator would roll back those batches. While violators exist, the rule for new successes is held by terminalization (`0018:436`) and by conformance. The "optional → required" paragraph and the 01 `0018` row say the same. Proposed ruling 6's last sentence becomes: "It becomes required through a check added only at zero violators, never `NOT VALID` over a violating row." | spec (migrations `0016:482`, `0017:320`, `0018:436/513`) |
| 1-RIS-1 | **The `tests/d` row above is withdrawn** ("392 passed, 5 xfailed … nothing touched the port"). That statement was false. The recorded command set no `INFRX_D_TASK`, so `pgharness.py:54` resolved to D1's namespace: container `infrx-d1-postgres`, host port 55432, database `infrx_d1`. The 332 PG-backed cases in that selection cannot pass without a live PostgreSQL. I cannot now establish which container that run used. Docker events do not reach back that far. The count is not lane evidence. F2C has no tasklocal service (`local_services('f2c') == {}`; `INFRX_D_TASK=f2c` fails collection with `KeyError: 'postgres'`). So the lane's `tests/d` evidence is the isolated run: `DOCKER_HOST=unix:///nonexistent.sock uv run --frozen pytest -q tests/d -k "not mutant"` → exit 0, **65 passed, 332 skipped**, 273 deselected. No port, container or database was touched. `tests/d`, `infrx/state` and the migrations are not in this lane's diff. The PG-backed D suites for the `TerminalOutcome` change belong to D10, in D10's own namespace. | evidence correction |

### Commands (host Linux, at `16ce171a`)

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/contracts -k "not pg"` | 0 | 1162 passed, 6 deselected |
| `uv run --frozen pytest -q tests/contracts/v2/test_lifecycle.py` | 0 | 61 passed |
| `uv run --frozen python -m tests.contracts.mutants <every lc_/lcb_/acc_>` | 0 | **79/79 killed** (70 before + 9 new) |
| `uv run --frozen pytest -q tests --ignore=tests/d --ignore=tests/contracts -k "not mutant and not pg"` | 0 | 1666 passed, 15 skipped |
| `DOCKER_HOST=unix:///nonexistent.sock uv run --frozen pytest -q tests/d -k "not mutant"` | 0 | 65 passed, 332 skipped (isolated; no PostgreSQL) |
| `node --test "tests/contracts/**/*.test.ts"` | 0 | 159 passed |
| `make console-test` | 0 | 297 passed |
| `make console-lint` / `make console-typecheck` | 0 / 0 | 0 errors (the same 2 warnings) / clean |
| `node tests/contracts/run-mutants.mjs --self-test` / `--entry v2` | 0 / 0 | 14/14; **44/44 killed** (41 + V2-LC-12..14) |
| `uv run --frozen python -m infrx.contracts.conformance.acceptance --write`, then `acceptance.replay(lifecycle_factory)` | 0 | `f2c-lifecycle-acceptance.2`, 24 recorded cases / 319 steps (was .1, 281 steps); replay `[]` |

**F2C fixture hash.** Run from `infrx/contracts/fixtures`: `sha256sum v2/lifecycle_*.json v2/result_read_cases.json v1/terminal_success_expiring.json acceptance/lifecycle.json | sort -k2 | sha256sum`. That command reproduces `da29215d…` at `1b9c7411`, so it is the recorded method. The new hash is **`63ccdd1c91d416c073619fad650001424dfad362266d977f705cd767a5d9c638`**. Transcript alone: `f91767adb20e0b2441ac5e20b56dd5c9dc1f587a385913cb11e59320aa45de88`. `result_read_cases.json`: `0efe3583…1131`.

**Merge compatibility.** `git merge-tree --write-tree claude/consumer-v1 codex/f2c-lifecycle`, with `claude/consumer-v1` now at `abb9fd69` (the brief named `bc43b6fb`), gives two conflicts. Both are export lists, and both resolve as a union:
- `apps/infrx-api/infrx/contracts/v2/__init__.py:36` → `_SUBMODULES = ("fixtures", "lifecycle", "money_units", "ports", "published_fixtures", "published_model", "records")`;
- `apps/app/lib/contracts/v2/types.ts:35` → keep both `export * from "./published-model.ts";` and `export * from "./lifecycle.ts";`.

This lane does not merge. The coordinator resolves both at integration.

**Open issues.** Unchanged from the list above, with one correction: the PG-backed D suites were run by nobody in this lane (1-RIS-1). A strict replay against D10 needs D10 to restart the fence at 1 for each generation, or to replay with a transcript regenerated from its own counters. 02 D4 now states the per-generation fence.

**Remaining effort.** Review fixes only. Optimistic 0.25 h, likely 0.5 h, pessimistic 2 h; confidence medium. Basis: every listed finding is closed, and each has a killed mutant or a column-level spec.
