# Lab datasets, evaluation, improvement and rollout handoffs

These tasks make Lab M2–M4 executable. They extend the audited plan; none is implemented by this document. Use imported provider-owned data to start M2 without waiting for production capture or a live judge. The manifest owns dependencies/status; these briefs own the detailed acceptance boundary.

Read [the complete build plan](12-complete-build-plan.md), [pending inputs](15-pending-inputs.md), [task manifest](tasks.json) and [verification oracles](04-verification.md).

## Shared implementation rules

Each numbered slice is a review checkpoint targeting 2–8 hours. Split larger slices before assignment without replacing the parent acceptance criteria. UI/backend subdirectories may be assigned separately after a reviewed contract commit; shared migrations, global contracts, navigation, composition and lockfiles retain a single owner. A task cannot be integrated solely against fakes. Long jobs use backend workers. New paths below are proposed ownership, not existing code.

## F3 — Freeze dataset, evaluation, training and rollout contracts

**Owner:** F. **Milestone:** LAB-M2. **Start:** F2P. **Real integration:** own actual-adapter acceptance. **Status:** planned.

**Owned paths:** `shared contract packages/fixtures (coordinator publishes)`.

### Implementation slices

1. F3.a: Define immutable dataset/sample/source/grant IDs, schema versions, lineage and split manifests; fixtures include text, finite video and structured tool I/O.
2. F3.b: Define evaluation/case/attempt/result, harness, checkpoint, annotation and external-run state transitions; stable idempotency keys, unit-tagged budgets and ambiguous-submit state.
3. F3.c: Define rollout policy/assignment/decision and optimization variant references; publish Python/TypeScript validators and fake adapters with rejected examples.

### Acceptance and failure proof

All feature teams consume one reviewed schema/fixture commit. Existing serving contracts remain compatible; optional features default off. Specify server-derived identity and permission checks at scheduling, access, export and external submission.

Cross-provider refs, mutable version refs, mixed USD/CREDIT and unknown schemas fail; role alone cannot authorize data reuse. Run fake/real conformance with the same fixtures.

**Oracles:** DATA-RIGHTS, EVAL-REPRO, PIPELINE-LINEAGE, ROLLOUT-PIN. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## D7 — Persist datasets, harnesses and evaluation coordination

**Owner:** D. **Milestone:** LAB-M2. **Start:** F3, D1R. **Real integration:** own actual-adapter acceptance. **Status:** planned.

**Owned paths:** `apps/app/supabase/migrations/`; `apps/infrx-api/infrx/state/lab_data.py (proposed)`; `apps/infrx-api/tests/d/`.

### Implementation slices

1. D7.a: Add source/grant references, immutable manifests, sample membership and split assignments, harness revisions and provenance using additive migrations.
2. D7.b: Add evaluation runs/cases/attempt leases/outbox/results/checkpoint receipts and unique keys; repository mutations enforce fencing and transitions.
3. D7.c: Add scoped RPCs/RLS/indexes and large-fixture query plans; run upgrade and two-provider role matrices against actual PostgreSQL/PostgREST.

### Acceptance and failure proof

Immutable version publication is atomic; partial uploads remain invisible. One logical result per run/case/evaluator; receipts and reservations survive restart. Tenant filters do not rely on the UI.

Race two publishers, duplicate checkpoint delivery and stale worker writes; kill around commit/ack and recover once. Foreign IDs and stale content grants fail actual DB/route tests.

**Oracles:** DATA-IMMUTABLE, DATA-RIGHTS, EVAL-DURABLE. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## N1 — Import benchmark data and existing annotation outputs

**Owner:** N. **Milestone:** LAB-M2. **Start:** F3. **Real integration:** D7, M3, L2. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/datasets/imports/ (proposed)`; `apps/infrx-api/tests/n/`.

### Implementation slices

1. N1.a: Implement versioned JSONL plus manifest importer, field mappings and schema preview for text, finite video/time spans and structured outputs; preserve original artifacts and annotation method/version.
2. N1.b: Stream bounded validation, resumable staging, content digests and dedup candidates through the existing media/object ports; quarantine malformed rows with row-level diagnostics.
3. N1.c: Publish validated immutable manifests through D7; record source/license/use restrictions and source clock units; provide provider pipeline export examples.

### Acceptance and failure proof

Provider can import an owned benchmark and a SAM/Gemini-style exported annotation fixture without replacing their pipeline or installing their tools. Publication reports accepted/rejected counts and cannot silently omit errors.

Oversized/archive traversal/SSRF sources, invalid timestamp units, duplicate upload replay and cross-provider objects are rejected. Process crash cannot publish half a dataset.

**Oracles:** DATA-IMPORT, DATA-RIGHTS. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## N2 — Version, split and export reproducible datasets

**Owner:** N. **Milestone:** LAB-M2. **Start:** N1. **Real integration:** D7. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/datasets/versions/ (proposed)`; `apps/infrx-api/tests/n/`.

### Implementation slices

1. N2.a: Implement content-addressed manifests and deterministic dedup/split policy; group by source episode/session/customer as applicable, including near-duplicate review flags.
2. N2.b: Freeze train/dev/test assignments and permitted purposes; implement new-version derivation and holdout access rules without mutating source evidence.
3. N2.c: Implement authorized resumable exports with manifest hash, redaction, TTL and per-source lineage; expose export cancellation and expiry.

### Acceptance and failure proof

Same inputs/policy/seed produce the same version and split digest. Baselines and candidates use a frozen holdout; adding samples creates a new version. Export includes exact schema/rights and omitted-item reasons.

Related clips or sessions cannot cross splits; revoked source blocks new reads/exports even if its immutable manifest survives as inaccessible metadata. Duplicate imports cannot silently change the holdout.

**Oracles:** DATA-IMMUTABLE, DATA-SPLIT, DATA-RIGHTS. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## N3 — Derive datasets from permitted traces and propagate revocation

**Owner:** N. **Milestone:** LAB-M3. **Start:** N2. **Real integration:** T3, C2, D6F. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/datasets/lineage/ (proposed)`; `apps/infrx-api/tests/n/`.

### Implementation slices

1. N3.a: Select traces/reviews using server-bound source and purpose grants; preserve original request/output/time spans and append corrections as separate records.
2. N3.b: Record transitive source-to-dataset-to-run-to-artifact lineage; implement current authorization checks and revocation/expiry events for queued and running jobs.
3. N3.c: Block affected future access/export/training and mark dependent artifacts restricted; provide deletion/reconciliation evidence and operator disposition for already delivered data/models.

### Acceptance and failure proof

Capture, provider analysis, annotation, external egress and training are independent purposes. Logical denial is immediate; physical purge follows recorded retention jobs. Never claim a trained model has been unlearned or an external export recalled.

Revoke a source after selection, during queue wait and before external submission; no new unauthorized egress. Missing projection cannot substitute for permission; retained aggregate evidence contains no revoked content.

**Oracles:** DATA-RIGHTS, DATA-LINEAGE. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## N4 — Build dataset import, version and split workflows in Lab

**Owner:** N. **Milestone:** LAB-M2. **Start:** F3, L1. **Real integration:** N2. **Status:** planned.

**Owned paths:** `apps/lab/app/(provider)/datasets/ (proposed)`; `apps/lab/lib/services/datasets/ (proposed)`; `apps/lab/tests/n/`.

### Implementation slices

1. N4.a: Build import mapping/validation progress with rejected-row downloads and resumable state.
2. N4.b: Build version/sample/timeline inspection, split summary, permissions and provenance views.
3. N4.c: Connect real repositories and authorized exports; support accessible loading/empty/error/denied states and restricted-version explanations.

### Acceptance and failure proof

A provider completes import → frozen version → inspect split → export through the UI. No placeholder navigation is published; data loading and failed authorization never render success.

Cross-provider deep links, revoked media URLs, interrupted imports and malformed mappings are exercised through real server actions; browser never receives privileged storage credentials.

**Oracles:** DATA-IMPORT, DATA-SPLIT, CONSOLE-FLOWS. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## H1 — Version prompts and bounded replay harnesses

**Owner:** H. **Milestone:** LAB-M2. **Start:** F3. **Real integration:** L2. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/harnesses/ (proposed)`; `apps/infrx-api/tests/h/`.

### Implementation slices

1. H1.a: Implement versioned prompt, processor, input mapping, tool schema and reference-output configuration with immutable hashes.
2. H1.b: Provide built-in text/finite-video/structured-output adapters and recorded or mocked tool-response replay; forbid arbitrary uploaded code and production side effects.
3. H1.c: Bind harness revision into serving/evaluation manifests and report unsupported tool/environment coverage; add single-factor and explicitly tagged multifactor comparisons.

### Acceptance and failure proof

Changing a prompt/processor changes the manifest identity. An unchanged candidate can be compared to a prompt-only variant. Unsupported replay behavior is visible and cannot silently invoke production tools.

Attempt tool network mutation, actuator execution, missing recorded response and prompt/config mutation mid-run; block or mark unsupported rather than produce falsely comparable scores.

**Oracles:** HARNESS-SAFE, EVAL-REPRO. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## B1 — Execute durable offline evaluation runs

**Owner:** B. **Milestone:** LAB-M2. **Start:** F3. **Real integration:** D7, N2, H1, L3, W2, D5. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/evaluation/runner/ (proposed)`; `apps/infrx-api/tests/b/`.

### Implementation slices

1. B1.a: Freeze dataset, serving/harness/evaluator revisions, seed, limits, rights snapshot and execution environment; create durable case jobs via D7.
2. B1.b: Dispatch to approved internal dev endpoints with separately funded provider_dev CREDIT, not consumer grants; record external USD separately when applicable. Integrate leases/cancel/retry/results and bounded concurrency.
3. B1.c: Implement deterministic task metrics, per-case outputs/errors/usage/timing and resume/reconciliation; store attempts without counting a case twice.

### Acceptance and failure proof

Both baseline and candidate run on identical frozen cases. Every scheduled case ends with a result, explicit failure, cancellation or unresolved state; costs cover attempts and missing outputs remain in the denominator. A retry is permitted only under the declared idempotency/side-effect contract.

Crash after submission/before acknowledgement, expire worker lease, revoke data, exhaust budget and cancel mid-batch; no ambiguous resubmission, unbounded spending, silent dropped case or stale result overwrite.

**Oracles:** EVAL-DURABLE, EVAL-REPRO. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## B2 — Compare quality, costs and latency with honest uncertainty

**Owner:** B. **Milestone:** LAB-M2. **Start:** F3. **Real integration:** B1. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/evaluation/reports/ (proposed)`; `apps/infrx-api/tests/b/`.

### Implementation slices

1. B2.a: Pair exact case IDs and dataset/harness/evaluator pins; reject incompatible runs or explicitly label unpaired/multifactor comparisons.
2. B2.b: Compute task and required-slice scores including errors/missing results, clustered-by-source uncertainty where appropriate, latency distributions and unit-separated cost totals.
3. B2.c: Store predeclared acceptance/non-inferiority thresholds and evaluation protocol with decision evidence; return inconclusive for insufficient data, mismatched coverage or uncertainty crossing the threshold.

### Acceptance and failure proof

A failing safety/SOP slice cannot be hidden by aggregate improvement; small samples do not become a confident win. Show observed facts separately from estimates and teacher judgments. Thresholds are workload inputs, not invented platform defaults.

Inject missing failures, duplicate cases, unequal case sets, skewed source clusters and a severe slice regression; report denominator/coverage and refuse unsupported improvement claims.

**Oracles:** EVAL-COMPARE. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## B3 — Benchmark externally produced checkpoints continuously

**Owner:** B. **Milestone:** LAB-M2. **Start:** F3. **Real integration:** B1, L2. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/evaluation/checkpoints/ (proposed)`; `apps/infrx-api/tests/b/`.

### Implementation slices

1. B3.a: Implement authenticated signed checkpoint event/manual import with replay window, provider binding, artifact digest and external run reference.
2. B3.b: Validate artifact registration through supported registry adapters; define pinned suite subscription, debounce/latest-only policy, concurrent-run and spending limits.
3. B3.c: Persist receipt → registration → evaluation lineage; expose failed/skipped/superseded events and reconcile partial failures without re-submitting paid work.

### Acceptance and failure proof

An external training loop can submit a checkpoint and obtain one evaluation per subscription/version. Arrival order does not silently redefine latest; no checkpoint is automatically public or promoted.

Replay/forge/reorder events, mutate artifact bytes under a URL, burst many checkpoints and crash between receipt/dispatch; stable dedup, bounded budget and visible skipped states.

**Oracles:** CHECKPOINT-IDEM. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## B4 — Build evaluations and experiment comparisons in Lab

**Owner:** B. **Milestone:** LAB-M2. **Start:** F3, L1. **Real integration:** B2, B3, H1. **Status:** planned.

**Owned paths:** `apps/lab/app/(provider)/evaluations/ (proposed)`; `apps/lab/app/(provider)/experiments/ (proposed)`; `apps/lab/lib/services/evaluation/ (proposed)`; `apps/lab/tests/b/`.

### Implementation slices

1. B4.a: Build baseline/candidate launch with frozen inputs, budget, test suite and predeclared criteria; expose harness version editor through validated fields.
2. B4.b: Build durable progress/cancel/resume, checkpoint event status and result drilldown with failure denominators and timeline evidence.
3. B4.c: Build comparison/slice/uncertainty/cost panels and reproducibility export; connect real authorized services.

### Acceptance and failure proof

A provider imports a benchmark, runs a baseline and candidate, inspects failures and exports a reproducible comparison. Live jobs do not execute in Next.js handlers; inaccessible evidence is not embedded in responses.

Reload during run, forbidden deep links, failed/cancelled cases and inconclusive results remain accurate in browser tests; CREDIT and USD are never summed.

**Oracles:** EVAL-COMPARE, CONSOLE-FLOWS. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## D8 — Persist annotations and external training lifecycle

**Owner:** D. **Milestone:** LAB-M3. **Start:** F3, D7. **Real integration:** D6J. **Status:** planned.

**Owned paths:** `apps/app/supabase/migrations/`; `apps/infrx-api/infrx/state/lab_pipeline.py (proposed)`; `apps/infrx-api/tests/d/`.

### Implementation slices

1. D8.a: Add append-only labels/review decisions, annotation batches and external training run/config/artifact lineage.
2. D8.b: Reuse the shared exact external USD reservation/accounting discipline with purpose-specific authorization; add idempotent callbacks, unknown-submit reconciliation and fenced worker mutations.
3. D8.c: Test role matrices, upgrade compatibility and cancellation/receipt/settlement races with actual services.

### Acceptance and failure proof

Review provenance cannot be forged by consumers; human ground truth and synthetic labels remain distinct. One budget reservation per logical operation includes outstanding/unknown submissions.

Duplicate callback, revoke consent before submit, simultaneous spends and stale workers cannot double settle, reclassify synthetic labels or expose another provider’s datasets.

**Oracles:** PIPELINE-LINEAGE, PIPELINE-BUDGET, DATA-RIGHTS. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## P1 — Import, review and export annotation records

**Owner:** P. **Milestone:** LAB-M3. **Start:** F3. **Real integration:** D8, N2. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/pipelines/annotations/ (proposed)`; `apps/infrx-api/tests/p/`.

### Implementation slices

1. P1.a: Import human/pipeline labels preserving annotator/method/model/prompt/version/confidence and time spans; link original evidence, never overwrite it.
2. P1.b: Add review assignment/disagreement/adjudication transitions with role checks and rubric versions; select authorized examples into new datasets.
3. P1.c: Export task examples or preference pairs with exact train-only membership and target adapter schema; holdout labels cannot leak into training exports.

### Acceptance and failure proof

Existing annotation pipeline output and later human correction round-trip with provenance. App feedback is evidence, not privileged calibration truth. Record rejected label mappings and missing evidence.

Forge reviewer role, relabel synthetic output as human truth, replay a correction or include holdout descendants in training export; deny or quarantine and retain original history.

**Oracles:** PIPELINE-LINEAGE, DATA-SPLIT. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## P2 — Run bounded teacher annotation batches

**Owner:** P. **Milestone:** LAB-M3. **Start:** F3. **Real integration:** P1, D8, N2, J2, J3. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/pipelines/teachers/ (proposed)`; `apps/infrx-api/tests/p/`.

### Implementation slices

1. P2.a: Build teacher batch manifests with authorized source purposes, redaction, rubric/prompt/model pins and dry-run cost estimates.
2. P2.b: Adapt the existing calibrated judge/external-budget ports to labels/preferences while keeping evaluation and training purposes separate; validate schemas and per-item failures.
3. P2.c: Reconcile callbacks/polls/unknown submissions; review sampled labels before a new dataset version becomes eligible for training.

### Acceptance and failure proof

Dry-run works without approved rates or external calls. Live execution requires a selected adapter/model, approved rate, source permission and budget. Teacher accuracy is evaluated on independent reviewed cases; no automatic ground-truth designation.

Timeout after provider accepts, change consent/rate while queued, malformed labels, partial output and budget exhaustion cannot cause duplicate charges or unauthorized egress.

**Oracles:** PIPELINE-BUDGET, PIPELINE-LINEAGE. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## P3 — Integrate external training and import candidates

**Owner:** P. **Milestone:** LAB-M3. **Start:** F3. **Real integration:** D8, N2, B3, L2. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/pipelines/training/ (proposed)`; `apps/infrx-api/tests/p/`.

### Implementation slices

1. P3.a: Implement an export/manual-run connector as the initial real workflow: immutable authorized train/dev manifests, configuration/objective/adapter metadata, environment and run reference; external training remains provider-owned.
2. P3.b: Implement connector interface plus protocol test server for submit/status/cancel/checkpoints, idempotency and ambiguous-submit reconciliation. Advertise automatic submission only for adapters with real integration evidence.
3. P3.c: Import returned checkpoint via B3 and enforce held-out evaluation before candidate approval; preserve actual provider-reported cost and unknown states without invented estimates.

### Acceptance and failure proof

A provider exports a training bundle, runs their existing SFT/LoRA or compatible preference workflow, imports a checkpoint and compares it on the frozen holdout. Managed RLHF, remote uploaded code and an unselected paid vendor are not required. LoRA is an adaptation method, not a competing objective to SFT.

Missing/incompatible artifacts, duplicate checkpoint/callback, cancelled run returning a late checkpoint and unknown provider submission cannot auto-promote, duplicate paid work or lose lineage.

**Oracles:** TRAIN-RECOVER, PIPELINE-LINEAGE. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## P4 — Build annotation and training workflows in Lab

**Owner:** P. **Milestone:** LAB-M3. **Start:** F3, L1. **Real integration:** P1, P2, P3, B2. **Status:** planned.

**Owned paths:** `apps/lab/app/(provider)/annotations/ (proposed)`; `apps/lab/app/(provider)/training/ (proposed)`; `apps/lab/lib/services/pipelines/ (proposed)`; `apps/lab/tests/p/`.

### Implementation slices

1. P4.a: Build review/import/export with disagreement, lineage and source-purpose restrictions.
2. P4.b: Build teacher dry-run/budget approval and batch status using supported adapters; make unavailable live capabilities explicit.
3. P4.c: Build external training bundle, run/checkpoint tracking and held-out comparison links; separate observed training loss from deployment-quality evidence.

### Acceptance and failure proof

Provider can complete a data correction → training export → external checkpoint → held-out comparison journey. No nonfunctional managed-training buttons or success state for unknown paid jobs.

Unauthorized reviewer, revoked source, unknown job, partial artifact and comparison regression surface actionable states and cannot be bypassed by direct server action calls.

**Oracles:** PIPELINE-LINEAGE, CONSOLE-FLOWS. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## D9 — Persist release policies and stable experiment assignment

**Owner:** D. **Milestone:** LAB-M4. **Start:** F3, D7. **Real integration:** own actual-adapter acceptance. **Status:** planned.

**Owned paths:** `apps/app/supabase/migrations/`; `apps/infrx-api/infrx/state/lab_rollout.py (proposed)`; `apps/infrx-api/tests/d/`.

### Implementation slices

1. D9.a: Add immutable release experiment/policy revisions, allowed variants, allocation unit and limits; avoid storing raw customer identities in provider-visible records.
2. D9.b: Persist assignment/decision references and policy events with compare-and-swap transitions; admissions retain chosen serving/rate revisions.
3. D9.c: Verify role isolation, upgrade compatibility and conflicting publish/rollback transactions under real PostgreSQL.

### Acceptance and failure proof

D8/D9 migration ordering is reserved by the sole D owner; independent feature scheduling does not permit migration filename collisions. Rollback creates a new policy decision, not edits to history.

Concurrent promotions, stale policy publisher and repeated assignment cannot mutate admitted jobs or allocate beyond defined policy. Pinned clients remain pinned.

**Oracles:** ROLLOUT-PIN, ROLLOUT-RECOVER. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## R1 — Route bounded shadow, canary and A/B experiments

**Owner:** R. **Milestone:** LAB-M4. **Start:** F3. **Real integration:** D9, L3, G2, G3. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/rollouts/routing/ (proposed)`; `apps/infrx-api/tests/r/`.

### Implementation slices

1. R1.a: Implement stable assignment by declared user/session/episode/request unit; honor explicit serving pins and eligibility/consent restrictions.
2. R1.b: Add G-owned admission integration through a coordinator request; freeze policy/variant/rate in job context, cap candidate traffic and separately fund shadow inference.
3. R1.c: Implement shadow response suppression and no-side-effect execution; emit cohort/coverage/loss telemetry without granting providers customer identity access.

### Acceptance and failure proof

Shadow does not change user output or debit their wallet twice; provider-funded duplicate work has its own limits. Canary and A/B use declared stable cohorts; video/robot episode assignment requires the relevant extension contract.

Retry admissions, change weights mid-session, revoke eligibility and overload candidate; no duplicate consumer billing, assignment drift for promised cohort, accidental shadow output or production tool execution.

**Oracles:** ROLLOUT-PIN. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## R2 — Evaluate guardrails and roll back controlled releases

**Owner:** R. **Milestone:** LAB-M4. **Start:** R1. **Real integration:** B2, L4. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/rollouts/control/ (proposed)`; `apps/infrx-api/tests/r/`.

### Implementation slices

1. R2.a: Freeze operational/quality thresholds, observation window, minimum evidence and decision protocol before launch; predeclare sequential checks or a fixed horizon.
2. R2.b: Compute cohort balance/coverage/drift/latency/error/budget guardrails; insufficient or delayed evidence cannot approve promotion.
3. R2.c: Implement operator approval and automated stop/rollback for declared triggers; prove queued/running requests stay pinned while future admissions revert.

### Acceptance and failure proof

Repeated peeking does not become an unqualified statistical win. Failure to collect quality telemetry halts expansion; operational emergency rollback remains available independent of model judges.

Kill controller, delay metrics, skew cohorts, breach a slice or fail candidate health; one audited decision, bounded exposure and correct future routing after recovery.

**Oracles:** ROLLOUT-RECOVER, EVAL-COMPARE. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## R3 — Register and compare optimized serving variants

**Owner:** R. **Milestone:** LAB-M4. **Start:** F3. **Real integration:** H1, B2, L2, W3. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/rollouts/optimization/ (proposed)`; `apps/infrx-api/tests/r/`; `models/ optimization evidence via experiment owners`.

### Implementation slices

1. R3.a: Register externally produced quantized/pruned/adapter/speculative/runtime/hardware variants as immutable serving revisions with compatibility and provenance metadata.
2. R3.b: Run unchanged held-out corpus and measured load profile against reference/variant, pinning engine build, dtype, hardware and preprocessing; report output quality and supported capability differences.
3. R3.c: Attach measured cost/throughput/latency/memory and rollback evidence to candidate comparison; import benchmark results from internal optimization work without building a general kernel platform.

### Acceptance and failure proof

Offline fixture/reporting integration works locally; actual optimization or non-NVIDIA support is claimed only for a measured supported adapter. Re-registering a checkpoint with a different engine/hardware/preprocessor creates a distinct serving identity.

Variant lies about capability, changes tokenizer or uses different corpus/load regime; reject equivalence claims. Better throughput cannot override a failed required quality slice.

**Oracles:** OPT-PARITY. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## R4 — Build release experiments and optimization comparison UI

**Owner:** R. **Milestone:** LAB-M4. **Start:** F3, L1. **Real integration:** R2, R3, B2. **Status:** planned.

**Owned paths:** `apps/lab/app/(provider)/releases/ (proposed)`; `apps/lab/app/(provider)/optimizations/ (proposed)`; `apps/lab/lib/services/rollouts/ (proposed)`; `apps/lab/tests/r/`.

### Implementation slices

1. R4.a: Build variant eligibility, cohort/budget/threshold setup and approval preview.
2. R4.b: Build traffic/quality/uncertainty progress, stopped/inconclusive states and audited rollback controls.
3. R4.c: Build optimization evidence comparison with explicit hardware/runtime scope; integrate real services and enforce operator-only public changes.

### Acceptance and failure proof

Provider proposes and operator controls a bounded release, sees reasons for stop/inconclusive decisions, and can inspect rollback lineage. UI does not label synthetic performance fixtures as measurements.

Role escalation through direct actions, stale revision approval, empty metrics and double-click rollback cannot corrupt policy or bypass publication controls.

**Oracles:** ROLLOUT-PIN, CONSOLE-FLOWS. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## I5 — Package dataset and evaluation workers for independent deployment

**Owner:** I. **Milestone:** LAB-M2. **Start:** F3, I1. **Real integration:** N2, B1, B3. **Status:** planned.

**Owned paths:** `infra/lab/ (proposed)`; `apps/infrx-api/deploy/lab/ (proposed)`.

### Implementation slices

1. I5.a: Package dataset/evaluation/checkpoint processes with explicit capability flags, queues, secrets, bounded concurrency and health/readiness.
2. I5.b: Add local real-service composition, object/DB backup-restore and queue/case recovery drills with owned test resources.
3. I5.c: Produce disabled-by-default staging deployment/rollback runbook, metrics and separate budget alerts; execute staging only when allocated.

### Acceptance and failure proof

No long task runs in a frontend process. Lab worker outage does not block consumer inference. Local composition is reproducible; hosted evidence is separately recorded per enabled feature.

Restart workers while jobs are active, lose object store, revoke credentials or fill spool/temp disk; no stale lease commits, unbounded retry or public inference outage.

**Oracles:** LAB-WORKERS. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## I6 — Package annotation and training integration workers

**Owner:** I. **Milestone:** LAB-M3. **Start:** I5. **Real integration:** P2, P3. **Status:** planned.

**Owned paths:** `infra/lab/ (proposed)`; `apps/infrx-api/deploy/lab/ (proposed)`.

### Implementation slices

1. I6.a: Add teacher/training process roles, allowlisted egress and purpose-specific secrets without enabling paid execution by default.
2. I6.b: Add reconciliation, budget alerts, dead-letter/operator actions and revocation cancellation runbooks.
3. I6.c: Exercise local protocol-server failure/restart drills and document exact remaining adapter/staging inputs.

### Acceptance and failure proof

Dry-run and manual export/import work without external provider secrets. Unknown submission is visible and requires reconciliation; budget and data-use restrictions survive process restart.

Restart after ambiguous submission, revoke a source while queued and fail secret lookup; worker does not double-submit or silently enable another provider.

**Oracles:** LAB-WORKERS, PIPELINE-BUDGET. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## I7 — Package release controls and optimization evidence operations

**Owner:** I. **Milestone:** LAB-M4. **Start:** I5. **Real integration:** R2, R3. **Status:** planned.

**Owned paths:** `infra/lab/ (proposed)`; `apps/infrx-api/deploy/lab/ (proposed)`.

### Implementation slices

1. I7.a: Package rollout controller with independent readiness, current-policy recovery and emergency disable controls.
2. I7.b: Add cohort/guardrail observability, required-evidence freshness and rollback exercises.
3. I7.c: Document allocated staging/load environment and measured hardware matrix; keep unsupported variants disabled.

### Acceptance and failure proof

Losing Lab UI/controller never loses durable inference/accounting; current safe policy or explicit fail-closed admission behavior is documented and tested. No automatic capacity purchases.

Controller outage/partition and stale telemetry cannot expand traffic; rollback preserves admitted request pins and consumer balances.

**Oracles:** LAB-WORKERS, ROLLOUT-RECOVER. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## E6L — Prove imported benchmark to candidate decision end to end

**Owner:** E. **Milestone:** LAB-M2. **Start:** F3, E2R. **Real integration:** E3L, N2, N4, H1, B1, B2, B3, B4, I5. **Status:** planned.

**Owned paths:** `tests/integration/lab_evaluate/ (proposed)`; `apps/lab/tests/e2e/ (coordinator-assigned suites)`; `research/plan/evidence/e/`.

### Implementation slices

1. E6L.a: Publish owned text/tool and finite-video benchmark fixtures, related-source split traps, deterministic evaluator and synthetic model endpoints with known wins/regressions.
2. E6L.b: Run real DB/object/services/browser journey import → frozen split → baseline/candidate → report → checkpoint event → comparison; inject restart, duplicate and denied-access cases.
3. E6L.c: Demonstrate intentional defects are detected; attach environment/SHAs/artifact hashes and separately list staging/GPU/model-quality evidence still pending.

### Acceptance and failure proof

LAB-EVALUATE-LOCAL passes without production traces, teacher API or managed training. Actual model superiority is a separate measured claim. All LAB-08/09/10/11 requirements have exercised positive and negative paths.

The suite must fail if a missing candidate output is dropped, a split leaks, a checkpoint duplicates a run or a foreign dataset is accepted; do not use implementation-generated expectations.

**Oracles:** DATA-IMPORT, DATA-SPLIT, DATA-RIGHTS, EVAL-DURABLE, EVAL-REPRO, EVAL-COMPARE, CHECKPOINT-IDEM. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## E7L — Prove the second authorized improvement iteration

**Owner:** E. **Milestone:** LAB-M3. **Start:** E6L. **Real integration:** E5L, N3, P1, P2, P3, P4, I6. **Status:** planned.

**Owned paths:** `tests/integration/lab_improve/ (proposed)`; `apps/lab/tests/e2e/ (coordinator-assigned suites)`; `research/plan/evidence/e/`.

### Implementation slices

1. E7L.a: Drive permitted deployment evidence → human/teacher annotation → new dataset/train export → external checkpoint → frozen held-out evaluation.
2. E7L.b: Propose/approve the candidate through L3/L4 in private or allocated staging deployment, collect permitted evidence pinned to that new serving version, then repeat correction/export/checkpoint/evaluation for a second iteration; revoke source rights at queue/submit/export boundaries.
3. E7L.c: Run manual connector against real export/import artifacts and protocol server against automatic connector interface; separately record selected live adapter evidence when authorized.

### Acceptance and failure proof

Two iterations preserve source rights, original evidence, independent holdout and budget reconciliation. A seeded bad checkpoint is rejected for promotion even with improved training loss. No synthetic training fixture is presented as real model improvement.

Suite catches holdout leakage, relabeled synthetic truth, duplicate paid submission, missing transitive revocation and promotion from training loss alone.

**Oracles:** DATA-LINEAGE, PIPELINE-LINEAGE, PIPELINE-BUDGET, TRAIN-RECOVER. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## E8L — Prove controlled rollout and optimization evidence

**Owner:** E. **Milestone:** LAB-M4. **Start:** E6L. **Real integration:** R1, R2, R3, R4, I7. **Status:** planned.

**Owned paths:** `tests/integration/lab_rollout/ (proposed)`; `apps/lab/tests/e2e/ (coordinator-assigned suites)`; `research/plan/evidence/e/`.

### Implementation slices

1. E8L.a: Exercise two serving variants and explicit-pinned/unpinned clients through stable assignment, bounded shadow/canary and operator approval.
2. E8L.b: Inject health/quality/coverage/budget regression and controller restart; verify bounded exposure, no double consumer debit and rollback of future admissions only.
3. E8L.c: Verify optimization evidence scope and incompatible variant rejection; attach real GPU/hardware parity only for allocated supported targets.

### Acceptance and failure proof

LAB-ROLLOUT-LOCAL can pass without training integrations. A live rollout still requires staged inference/quality/latency evidence and approval for affected traffic; unsupported chip/model combinations remain unavailable.

Suite detects shadow output leakage, unstable session assignment, changed queued-job revision, unmeasured hardware claims and a regression hidden by aggregate scores.

**Oracles:** ROLLOUT-PIN, ROLLOUT-RECOVER, OPT-PARITY. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.
