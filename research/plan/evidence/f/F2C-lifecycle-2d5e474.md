# F2C-L slice a — durable lifecycle records and ports (evidence)

Lane F2C-L (task F2C slices a, b, d). This file covers **slice a only**; b and d follow on the same branch.

- Base: `dff31efc` (main). Head of the code commit: `2d5e4743`. Branch `codex/f2c-lifecycle`, worktree `.claude/worktrees/codex-f2c-lifecycle`.
- Scope: contracts only. No SQL, no route/adapter implementation, no composition change.

## Changed paths

| Path | What |
|---|---|
| `apps/infrx-api/infrx/contracts/v2/lifecycle.py` (new) | Records, `LifecycleRefusal` + `REFUSAL_ERRORS`, `UploadRepository`, `ReadinessStore`, `ContentLifecycle` Protocols, time/version authority |
| `apps/infrx-api/infrx/contracts/v2/__init__.py` | `lifecycle` submodule export |
| `apps/infrx-api/infrx/contracts/v2/fixtures.py` | 11 lifecycle builders/models + `lifecycle_refusals.json` table (same generator, same byte test) |
| `apps/infrx-api/infrx/contracts/fixtures/v2/lifecycle_*.json` (12 new) | generated fixtures |
| `apps/infrx-api/infrx/contracts/fakes/lifecycle.py` (new) | reference adapter of the three ports (shared `Durable` state; `reopen()` = another process) |
| `apps/infrx-api/infrx/contracts/fakes/factories.py` | `lifecycle_factory`, `V2_FACTORIES["lifecycle"]` |
| `apps/infrx-api/infrx/contracts/conformance/lifecycle.py` (new), `conformance/__init__.py` | 19 exported cases, `run_lifecycle_conformance`, `V2_SUITES["lifecycle"]` |
| `apps/infrx-api/tests/contracts/v2/test_lifecycle.py` (new) | cases vs fake, record rules, TS parity |
| `apps/infrx-api/tests/contracts/mutants.py`, `test_mutants.py` | 34 `lc_*` mutants; runner target + record-test module; 3 in the default subset |
| `apps/app/lib/contracts/v2/lifecycle.ts` (new), `v2/types.ts` | TS twin: vocabularies, refusal->code map, `UploadTicket`, `ReadinessView`, exact decoders |
| `apps/app/tests/contracts/v2/lifecycle.test.ts` (new), `tests/contracts/mutants.json` | console suite (reads the Python fixtures), 5 `V2-LC-*` mutants |
| `research/plan/02-durable-protocols.md`, `01-contracts.md` | appended F2C sections (decisions D1–D5, ports) |

## Ports (signature authority: `contracts/v2/lifecycle.py`)

- `UploadRepository`: `create(org_id, constraints: UploadConstraints) -> UploadTicket`; `acknowledge_put(org_id, upload_handle, *, bytes, digest) -> UploadTicket`; `complete(org_id, upload_handle, source: MediaRef) -> UploadTicket`; `abort(org_id, upload_handle, refusal) -> UploadTicket`; `resolve(org_id, upload_handle) -> UploadTicket`; `expire(limit) -> int`. Public body parsing: `UploadConstraints.parse(body, *, max_media_bytes, allowed_mime)`.
- `ReadinessStore`: `admit_ready(request, idem, expectation: AdmissionExpectation) -> (Admission | AdmissionV2, ExecutionReadiness | None)`; `readiness(job_id) -> ExecutionReadiness | None`; `claim_preparation(job_id, worker_id) -> Lease` (refuses `not_ready`).
- `ContentLifecycle`: `register(identity: ContentIdentity) -> ContentObject`; `references(content_id) -> tuple[ContentReference, ...]`; `candidates(*, after, limit) -> ContentPage`; `claim(content_id, generation, holder) -> DeletionClaim`; `tombstone(claim) -> Tombstone`; `acknowledge_delete(tombstone) -> ContentObject`.
- Refusals: 19 reasons, each an existing code (`lifecycle_refusals.json`); internal ones (`not_ready`, `not_eligible`, `reference_live`, `claim_held`, `claim_lost`) have no HTTP status. Reason on `DomainError.refusal`, never serialized.

## Commands (host Linux, worktree root unless noted)

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env |
| `cd apps/infrx-api && uv run --frozen pytest -q -p no:cacheprovider tests/contracts` (baseline at `dff31efc`) | 1 | 1075 passed, 3 failed |
| same, at `2d5e4743` | 1 | **1149 passed, 3 failed** — the same 3: `tests/contracts/v2/test_v1_projection_pg.py` raise `HarnessBusy` (shared PG port 55432 held by the `codex-e2c-verify` lane's run); environmental, not touched |
| `uv run --frozen pytest -q tests/contracts/v2 -k "not pg"` | 0 | 330 passed, 6 deselected |
| `uv run --frozen pytest -q tests/contracts/v2/test_lifecycle.py` | 0 | 48 passed |
| `uv run --frozen python -m tests.contracts.mutants <34 lc_* names>` | 0 | **34/34 killed** |
| `uv run --frozen pytest -q tests/m tests/w tests/g -k "not mutant"` (consumer sanity) | 0 | 1107 passed, 22 skipped, 164 deselected |
| `make console-test` | 0 | 294 passed, 0 failed |
| `node --test "tests/contracts/**/*.test.ts"` (apps/app; baseline 151) | 0 | 156 passed |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings (`conformance.ts`, `fake-services.ts`) |
| `make console-typecheck` | 0 | clean |
| `node tests/contracts/run-mutants.mjs --self-test` / `--entry v2` | 0 / 0 | 14/14 self-tests; **36/36 v2 mutants killed** (31 existing + 5 new) |

Fixture manifest hash (slice a, provisional; slice d records the versioned one): sha256 over `sha256sum` lines of the 12 sorted `fixtures/v2/lifecycle_*.json` = `6cbe122bb28358821ffce6d423bedd3952ec87216aec6472331440214bb67950`.

## Failed-then-passed regressions

- **Old behaviour reproduced** (scratch script over the real `infrx.media` code at base): a finalized `MediaUploads` upload resolved from a new `MediaUploads` over the same object store → `not_found` while the bytes remain (RV-02); `MediaStaging.attached()` in another process answers `None` for a text-only attach and for a never-attached job alike (RV-05).
- The same defects reintroduced into the reference adapter are **killed** by the named cases: `lc_reopen_loses_the_tickets` (per-process ticket state), `lc_empty_attach_writes_no_marker` and `lc_missing_marker_reads_as_empty` (RV-05 both directions), `lc_manifest_reference_ignored` / `lc_open_ticket_does_not_protect_its_destination` (RV-03 liveness without durable references).
- Mutation runs during development: first run 0/34 (runner could not collect — the test module read the TS file at import; made lazy); second run 32/34 — `lc_unknown_constraint_ignored` survived because `extra="forbid"` already refused unknown names (the redundant check was **deleted**, mutant replaced by one on the record config, now killed); `lc_open_at_expiry` died by the record's own window validator (declared `dies_by=ValidationError` with the reason). Console: `V2-LC-02` was equivalent (typed getters already refuse a missing field; removed); `V2-LC-04` survived until the test forged a digest on a ticket without declared constraints (strengthened; killed).

## Decisions D1–D5 (validated against `dff31efc`; recorded in 02 §F2C)

- **D1 confirmed with two amendments.** (a) *Objection to the backfill rule*: "text-only → ready" is not decidable in SQL — `infrx.jobs` stores no media count and zero `job_media` rows is exactly RV-05's ambiguity. Replacement: no backfill; marker-less `preparing` jobs are `not_ready` and end at `preparation_deadline_at` via the existing reaper (0016 `recover`, line 512) as `preparation_failed`/`released_free` (≤ 120 s default); queued/running jobs unaffected. (b) Only `admit_ready` writes the marker; previous-runtime `admit`/`admit_credit` never do (an old gateway overlapping a new worker would otherwise re-open RV-05).
- **D2 confirmed** (stable `content_id` per key; generation on re-registration after ack; `content_retiring` until ack; older-generation ack is a no-op). Added rule: claim TTL > object-store request timeout. 0010 `media_objects` compare-and-delete lacks generation/tombstone; extend additively.
- **D3 confirmed**; mechanics in slice b. Note: 0014's `job_results_immutable` trigger forbids any update — D10 needs a new guard permitting only the scrub transition.
- **D4 confirmed** (PG transaction time; `now < instant` is live; `retain_until` set once at terminalization; `jobs.py:156` recomputation is the slice-b/G7 fix).
- **D5 confirmed with conditions**: rollback holds only if `admit_ready` also writes 0003 `job_media` source rows (the previous worker reads them), previous-runtime ops never write markers, and each previous→new boundary pauses admission until no marker-less `preparing` job remains. `FinalizedSource.duration_s` is required to fit 0010's `media_uploads_finalized_facts`.

## Proposed rulings (coordinator appends; next free R109)

- **R109 — Lifecycle ports (F2C.a).** `UploadRepository`, `ReadinessStore` and `ContentLifecycle` are as encoded in `contracts/v2/lifecycle.py`. Their refusals are the closed `LifecycleRefusal` set, each raised as the existing error code in `REFUSAL_ERRORS` (`fixtures/v2/lifecycle_refusals.json` is the cross-language table); the reason travels on `DomainError.refusal` and is never serialized. No new public code or HTTP status.
- **R110 — One-phase execution readiness.** `admit_ready` records the exact source manifest and the execution-ready marker in the admission transaction, after checking the runtime's `AdmissionExpectation` (the approved card), the pinned serving revision's capability, and that each source is a live content row of the request's organization with the same digest (an upload also through its finalized, unexpired ticket). A refusal admits nothing. `sources: []` is a completed empty manifest; no marker is `not_ready`, never an empty manifest. `claim_preparation` refuses `not_ready`. `admit`/`admit_credit` never write a marker.
- **R111 — Readiness cutover without backfill.** A `preparing` job without a marker is not made ready by migration; it ends at its `preparation_deadline_at` as `preparation_failed`, released free. Each previous→new runtime boundary pauses admission until no marker-less `preparing` job remains. `admit_ready` also writes 0003 `job_media` source rows so the previous runtime remains a valid rollback.
- **R112 — Content lifecycle.** One durable row per `(location, object_key)` with a stable `content_id`; `generation` increments only on registration after an acknowledged delete; eligibility is persisted at the first registration (a `discovered` re-registration never resets it; a first discovery starts its own grace); `tombstone` rechecks references and grace under the row lock admission takes; a tombstoned key refuses new use (`content_retiring`) until `acknowledge_delete`; an acknowledgement for an older generation changes nothing; the claim TTL exceeds the object-store request timeout.
- **R113 — Lifecycle time authority.** Every lifecycle instant is PostgreSQL transaction time; an instant is live while `now < instant` and has passed at equality; a reference's `retain_until` is set once at its job's terminalization from persisted job facts and never recomputed from configuration.

## Wiring requests

None for slice a (nothing composed; ports are unwired by design). Consumer notes for the dependent lanes:

- **D10**: implement the three Protocols; run `run_lifecycle_conformance(factory)` with hooks `reopen`, `jobs`, `credit_balance`, `set_capability` (cases read every instant from returned records; any configured windows work with grace < upload window). 0010 `media_uploads` fits with additive nullable columns (receipt bytes/digest/at, finalized content id/generation; `aborted_reason` holds the refusal value). Dual-write 0003 `job_media` in `admit_ready`.
- **M5**: tickets through `UploadRepository`; `UploadConstraints.parse` at the route; `register` the destination before PUT and the source before its copy; `complete` with the measured `MediaRef` (duration required); `resolve` → rebuild the `MediaRef` with M's key builder.
- **W5**: replace the `work.media_refs`-gated attach wait (`preparation.py` `_media`) with `readiness(job_id)` and claim through `ReadinessStore.claim_preparation`.
- **G7**: `Relay.admit` → `admit_ready(prepared, idem, AdmissionExpectation(regime, active_rate_card_version))`; the post-commit `_admitted`/`_resume` rechecks and attach become in-transaction.

## Open issues

- `infrx/contracts/README.md`'s module table does not list `v2/lifecycle.py` yet (docstrings carry it); slice d updates the README with the consumer matrix.
- `SURFACE_VERSION` is unchanged (`contracts-v2.0`); a bump should be made once, when both F2C lanes merge, not per lane.
- Configured durations (upload window, grace, claim TTL, retention per kind) have no 08 §5 names yet; D10 proposes them. Retention values are P-25.
- Capability-in-transaction assumes the admission store can read the pinned serving revision's capability record (0007 registry); D10 confirms.

## Remaining effort (slices b and d)

Optimistic 5 h, likely 8 h, pessimistic 12 h; confidence medium. Basis: slice a (records, fake, 19 cases, 39 mutants, TS twin) took one session; b touches `TerminalOutcome` (v1 record used by every JobStore suite and adapter — old-record compatibility needs care), d is fixtures + a consumer matrix across six tracks.
