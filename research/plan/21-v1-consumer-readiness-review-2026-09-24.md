# 21 — Marlin consumer v1 readiness review

Reviewed on 2026-09-24: code/tests at **`d7dc3690`**, then refreshed to main **`726d004d`** when new E1B results arrived during review. That delta adds measurements and coordination notes, with no runtime-code change. This is a repository and release-evidence audit, followed by a proposed priority order for discussion. It does not declare BACKEND-READY, authorize a hosted cutover, or mark pending tasks complete.

Read this beside [handoff 20, especially §14](20-platform-handoff-2026-09-24.md), [the operational tail](../../HANDOFF-20260924T2115Z.md), and [tracker v46](evidence/coordinator/PROGRESS.md). Historical session records remain evidence for their own environments. Implementation-agent names have no bearing on the product architecture.

## 1. Decision supported by the review

**The durable inference backend is substantially implemented and has a deployed metered pilot. The consumer product is not ready for public self-service.** Its next milestone should be a bounded, observable Marlin service joined to a complete signup-to-result-to-balance journey. More models, Lab, automated training, fleet infrastructure and open-ended tuning do not belong on that critical path.

The intended consumer v1 remains:

1. An individual signs up and verifies their account.
2. They receive **10,000 CREDIT once**, with no recurring refill.
3. They see the actual Marlin capability, supported input limits and approved credit rates.
4. They create a personal consumer key and run a copyable example against one canonical endpoint.
5. They submit finite video clips, receive output or a durable job handle, and can recover results/retry safely.
6. They see real usage, reservations, settlement and remaining credits; revoke a key; understand failures and retention.
7. Operators can detect a failed service, reconcile accounting and recover it without losing or charging an accepted request twice.

For the robotics dataset use case, a manifest-driven client with per-item identifiers, bounded concurrency, retry/backoff, durable results and a resume file is sufficient initially. A rich dataset UI, SOP accuracy certification and native live-video/robot-control support are separate deliverables. Output SSE is not live-video input.

## 2. What landed, what is live, and what remains

| Area | What exists on main | Boundary of the evidence |
|---|---|---|
| Contracts and database | Product-v2 identity/CREDIT/serving contracts; migrations 0001–0018; personal entitlement, registry, holds, leases, journal, settlement and reconciliation | Hosted application of 0001–0018 is recorded in the operational tail. Do not rewrite them; the next migration number must be coordinated. |
| Gateway and jobs | Authenticated ingress, bounded media intake, catalog validation, sync/output-SSE, explicit async jobs, cancel/result/events, idempotent replay and upload routes | Real metered ingress is composed. This is no longer the old unmetered proxy. Upload lifecycle durability and public discovery remain gaps below. |
| Execution | Separate preparation and generation workers; exact engine token count; fenced execution, recovery and cancellation; pinned engine and shared prepared media | Preparation-loop omissions from earlier deployments were repaired. The deployed release is reported as `bda1586`; this audit did not inspect its running process identity. |
| Scheduling | PostgreSQL authority, dispatch outbox, Valkey queue indices and index rebuild/reconciliation | Real adapters and prior integration reports exist. Availability still depends on a single GPU host and its external database/storage. |
| Operator service | Headless catalog/key/grant/suspension/reconcile tools, installer, smoke, rollback/recovery scripts, metrics/rules/dashboard | A script and a metric are not an operating alert/restore service. Several hosted operational obligations remain open. |
| Performance | Distinct corpus, direct-engine and end-to-end clients, E1B/E4B runners, M4/W4 experiments, token-count memo and overload-drain repair | No qualifying W4 setting was adopted. Eight engine/worker slots are a provisional configuration, not a measured optimum. Latest certification results are not committed. |
| App | Existing login/session/key/catalog scaffolding; contract/service/view test foundations | A2/A3/C0/C3A/U1R/U2/U3 and consumer release gates remain planned. Production usage/balance views deliberately show unavailable, not real data. |
| Lab | Architecture, detailed module plans and shared primitives; `apps/lab/README.md` | No runnable provider product. Provider membership alone does not grant access to customer content. |

The manifest has **119 records: 39 implemented, 5 integrated, 69 planned and 6 superseded**. This is not a percentage of launch readiness: some implemented items are foundations behind fakes; others have later deployed evidence that the coarse status does not express. E1B remains planned despite partial measurements; E4B's implemented status describes the runner, not an accepted release.

### Reported hosted configuration

The operational tail records a single L40S pilot with engine/worker concurrency 8, large-body limit 8, finite-video cap **82 seconds**, model artifact `fd111fca…`, and the pinned vLLM image. The latest reported application revision is **`bda15866e5700f3856d7142580da842fba9bbd23`**; main's later commits include coordination/evidence.

It also records **`legacy_usd` accounting with CREDIT admission disabled**. One test user's 10,000 CREDIT and separate USD 5 test grant do not prove the consumer CREDIT journey. Public signup is disabled; the provisional CREDIT card is not an operator-approved public price. Historical USD and CREDIT must remain separate.

### Fresh, read-only public observations

At the local observation clock `2026-09-24T20:26Z`, `/health` returned 200, `/v1/models` returned a model document, and the App login returned 200 with “Accounts are created by invitation.” These observations prove reachability and visible content only. No customer authentication, GPU request, paid call, hosted database query or load test was performed. `/readyz` is deliberately private; public health is not proof of inference readiness.

The public model document advertised 120-second video, concurrency 16, tool-related parameters and `compliance.zdr: true`. These are legacy static claims, not the documented pilot's current capability/readiness policy.

### Latest acceptance results received during review

Main `726d004d` adds [E1B results on bda1586](../../models/marlin2b/results/E1B-box-bda1586/bench.jsonl). The three committed raw files agree with the summaries: each contains 120 attempts, **114 successes, 6 over-cap refusals, no failed attempts and no replayed identities**. This is positive evidence for the deployed intake repair, replacing the old ReadError observations for these cells.

| Cell | TTFT p95 | End-to-end p95 | Scope |
|---|---|---|---|
| 0.5 requests/s after engine restart | 8.66 s | 9.34 s | One tenant, mixed inline-video/text, 120 attempts |
| 1 request/s, warm engine | 5.21 s | 6.78 s | Same mix and sample count |
| Burst 8 at nominal 0.5 requests/s | 7.60 s | 9.91 s | Same mix and sample count |

These mixed-workload numbers are not video-only latency guarantees or a completed 32-request overload/4-hour soak gate. The new coordinator note reports run-3 identity/config and two in-cap SOP parity clips passing, with the remaining run still in progress. It also records `EMAXCONNSESSION`: runtime pools consumed the 15 session-pool slots; the certification launcher switched to the transaction-pool port. This strengthens RV-09's pool-sizing obligation rather than proving it fixed.

## 3. Review findings and required closure

Priorities below are relative to **public consumer launch**. Some are already known in handoff 20; the review rechecks their implications instead of presenting them as newly discovered defects.

### RV-01 — P1: published capability and retention claims contradict the runtime

[`gateway/routes/models.py`](../../apps/infrx-api/infrx/gateway/routes/models.py) reads `provider-models.json` directly and returns static readiness/capacity/privacy fields. It does not use the authenticated deployment catalog used by admission. The current public response still says two-minute videos and zero data retention. The [App docs](../../apps/app/app/(console)/docs/page.tsx) likewise tell users to trim to 120 seconds and say prompts/videos/completions are never stored.

The implemented runtime stages normalized request payloads and source/prepared media, persists request records and journals, and stores result content. Disabling optional traces does not remove this serving storage. The mismatch is observable now, not merely a future App issue.

**Closure:** A3 + G/D/M publish one capability/rate/retention projection from the active release. Update the OpenAI-style discovery response, any separate OpenRouter provider document, catalog and examples together. Remove unsupported tool/limit/readiness claims and describe actual serving retention separately from opt-in traces. Verify catalog claims against actual accepted/rejected requests. Do not label the current system ZDR.

### RV-02 — P1 for advertised uploads: upload handles do not survive gateway reconstruction

[`MediaUploads`](../../apps/infrx-api/infrx/media/uploads.py) stores lifecycle records in `self.uploads`; ownership resolution also needs local `self.refs`. [`pilot.build_ingress_deps`](../../apps/infrx-api/infrx/gateway/pilot.py) composes this implementation. `PgAttachments` persists already-admitted job attachments, not upload create/finalize state.

**Reproduced:** create, put and finalize a valid upload; construct a new `MediaUploads` against the same object store; resolving the handle returns `not_found` while the source bytes remain. Restarting the gateway between upload steps or before a resumed dataset submission can therefore invalidate a valid handle. D2's durable tables alone do not fix the unwired adapter.

**Closure:** M/D/G persist and reload upload state, constraints, expiry and immutable finalization. Prove completion retry, restart between every step, concurrent finalization and cross-tenant denial using the actual database and object-store adapters. Until then, do not promise restart-safe uploaded handles. URL/base64 requests remain an alternative for a deliberately restricted pilot; they do not close the promised upload feature.

### RV-03 — P1: retention is not operational; simply starting the collector is unsafe

Neither gateway nor worker lifetime starts [`MediaCollector`](../../apps/infrx-api/infrx/media/gc.py). Its liveness scan enumerates only `store.by_job` and `prepared_by_job`, not durable job/media references. A fresh process therefore knows nothing about another process's live objects. The upload destination sweep also uses only local upload records.

**Reproduced with shared in-memory object storage:** a reconstructed store and collector deleted the source after the grace interval without making any live-job or durable-attachment query. This demonstrates the missing durable scan; it is not a claim that production deleted an active customer's object. Production currently does not schedule this collector.

The collector's prefixes also do not establish expiry for staged payload objects or database request/result content. `infrx.job_results` explicitly delegates physical purging to a later retention sweep. Long-running dictionaries and local cache files need bounds too.

**Closure:** a D/M retention work package must enumerate durable references, expiry and live leases; fail closed on database failure; coordinate object deletion with admission/attachment races; enforce local cache bounds and cover payloads, media, results and retained request content. Schedule it only after restart/multi-process/live-job preservation drills pass. This is consumer serving-data work, not something to postpone wholesale to Lab trace task T3.

### RV-04 — P1 release gate: latest deployed fixes have no committed final certification

The operational tail reports `bda1586` deployed and smoke 15/15. Main now contains the three passing E1B cells above and E4B run-2 results on **`4226315`**, which exited 1, plus runner/overload repairs. No final `E4B-box-bda1586` report is present at `726d004d`; the updated session note says run 3 is still in progress. [The release decision](evidence/e/E4B-release-decision.md) remains an unfilled historical template with already-fixed blockers.

Run 2 includes real evidence, but also an interrupted four-hour soak, unknown GPU/reconciliation cells, transport ReadErrors under overload, insufficient short-clip percentile samples and runner/identity/parity/resume failures subsequently addressed in code. None can be treated as a pass on the new release merely because the repair merged.

**Closure:** retrieve the existing in-flight results first. Bind tree/image/model/profile/config hashes, accepted workload, overload, full soak, accounting drift, cancel/resume, parity and recovery to one release decision. Re-run only missing/invalidated cells. Explicitly resolve failed provisional targets with the workload owner; do not move thresholds after seeing the result and silently claim success.

### RV-05 — P1 before CREDIT cutover: finish the admission-to-worker authorization barrier

This is the known zero-media attach gap (handoff 20 D-19/R99), confirmed in source. [`PgAttachments.put`](../../apps/infrx-api/infrx/media/attachments.py) does nothing for an empty set; `get` cannot distinguish a text-only attachment from one never completed. [`PreparationRunner._prepare`](../../apps/infrx-api/infrx/worker/preparation.py) waits for attachment only when `work.media_refs` is nonempty. CREDIT's deployment/card/capability rechecks occur after the admission transaction in [`Relay._admitted`](../../apps/infrx-api/infrx/gateway/routes/relay.py).

Consequently the separate preparer has no durable completion marker to wait on for text-only acceptance. A gateway crash or interleaving can bypass the intended post-admission gate; D2's transactional authentication and wallet checks still apply. This is a code-path finding; a hosted CREDIT race was not run in this review.

**Closure:** D/G/W establish an explicit durable admission/attachment-ready boundary for zero and nonzero media, preferably making correctness-critical validation transactional. Prove crash-after-admit, delayed/rejected postchecks, alias/card changes and preparation racing with cancellation in CREDIT mode. Coordinate a new migration after 0018; do not edit applied migrations.

### RV-06 — P1 product completion: App is not joined to the implemented backend

[`consoleContext`](../../apps/app/app/(console)/usage/fake-console-context.ts) deliberately returns null in production. Usage and balance pages therefore cannot display consumer activity. The catalog/docs read the old `public.models` surface and USD fields. There is no public signup page or A1 entitlement invocation in the existing auth callback. Session code chooses the first org membership rather than the explicit v2 consumer identity context.

This does **not** mean all current authentication/key code is broken: the existing owner-only key action remains compatible because migration 0009 supplies consumer audience and individual defaults, and migration 0004 restricts browser-writable key columns. Reuse that implementation after applying the new service boundary.

**Closure:** A2/A3/C0/C3A/U1R/U2, with the narrow necessary U3 operator surface, then E3A/I2A/I3/E4. Prove fresh verified individual → one grant → key → actual finite-video output → one settled CREDIT debit → correct balance/usage → revoke/401. Duplicate callbacks, existing accounts, exhausted balance, active holds, failed execution and another tenant's data must be in the same journey gate.

### RV-07 — P2, blocking upload client acceptance: the benchmark tests its own incompatible protocol

[`bench.upload`](../../models/marlin2b/bench.py) sends `purpose`, `filename`, `sha256`, `content_type`, expects `url`/`handle` and sends completion fields. The [actual route](../../apps/infrx-api/infrx/gateway/routes/uploads.py) accepts `max_bytes`, `bytes`, `accepted_mime`, `digest`; returns `upload_handle`/`destination_ref`; uses an authenticated PUT to `/v1/uploads/{handle}`; and requires empty completion.

**Reproduced:** the benchmark against the actual mounted upload route returns 400 at create. Its existing fake-gateway suite still passes, so that suite does not establish client/server interoperability.

**Closure:** E/G/M align the client and fake to the frozen contract, including the authenticated destination; exercise the actual mounted route in client conformance and one hosted smoke. Include upload recovery in the dataset journey after RV-02 is fixed.

### RV-08 — P2, blocking performance claims: replayed responses remain in performance summaries

`bench.summarize` includes all `outcome == accepted` rows in throughput/latency aggregates even if `idempotency_replayed` is true. It reports a replay count separately. The earlier E1B reuse of identities already produced replay-only performance cells; the distinct-key rerun corrected those particular measurements.

**Closure:** performance runs must fail or be marked invalid when unexpected replay is nonzero, with explicit cold/warm/cache policy and fresh experiment identity. Dataset-resume verification must still permit intentional replay. Do not disable idempotency to obtain numbers. Local e2e timing work can be ported as a separate measurement change after defining whether encoding/upload/queue/retry time is included.

### RV-09 — P1 for public operation: monitoring and restore are not a running service yet

The handoff records unscheduled alert evaluation/delivery, a broad project database login, constrained session-pool capacity, ephemeral NVMe weights without a verified durable mirror, and an unconfirmed hosted backup/PITR policy. These are operational facts reported by the prior session, not fresh hosted checks here. Existing metrics, twenty alert rules and several successful restart/index-loss drills are valuable foundations.

**Closure:** I/D deploy continuous scrape/evaluation/delivery and synthetic inference checks, test that an alert reaches an owner, establish storage/queue/settlement/pool alarms, use an appropriately restricted runtime login, and prove restore from durable model/config/database storage. Record RTO/RPO and single-host downtime honestly. A root-disk snapshot does not protect instance-store model weights.

### RV-10 — P1 recovery gate: readiness-only rollback is not demonstrated usable inference

The operational tail explicitly says the latest backup contains **`27af05a`**, not the intended `4226315`; the old runtime was left by an earlier drill. Earlier releases had preparation-loop omissions. A five-second readiness observation on rollback is insufficient to establish a working end-to-end fallback; public recovery lag was separately uncertain.

**Closure:** retain a known-good immutable fallback bundle and verify public finite-video execution, result retrieval and settlement after rollback and forward recovery. Make backup identity and the operator's selected rollback revision explicit. Preserve maintenance mode when correctness is uncertain.

### RV-11 — P2 before publishing retention guarantees: result expiry has two authorities

D5 writes `jobs.result_expires_at`, but the terminal DTO omits it. [`Jobs.result_expiry`](../../apps/infrx-api/infrx/gateway/routes/jobs.py) recomputes from the *current* route configuration plus settlement time. Changing the configuration can change the reported/access expiry of an already completed result. Physical cleanup is a separate RV-03 obligation.

**Closure:** F/D/G carry the persisted expiry into the terminal result contract and apply it to status/result/replay consistently. Prove a config change cannot extend or shorten an already promised result lifetime unexpectedly.

### RV-12 — P2 verification portability: a fresh Mac session cannot reproduce the full gate

The full API run ended with **90 failures, 2,988 passes and 640 skips**. The gateway build-info test reads Linux `/proc/meminfo` directly, breaking its own case and the pristine baseline for 20 gateway mutants. Infrastructure/serving cases require Linux systemd and GNU utility/Bash behavior; their mutation lists inherit baseline failures. The release-bundle test also assumes `git init` does not default to `main`, then tries to fetch into a checked-out `main`. One W4 mutant reported survived; a broken platform prerequisite is not grounds to count that mutant killed.

**Closure:** E/I document and automate a supported Linux verification environment, explicitly distinguish platform prerequisites from product failures, fix the test's Git-default assumption, and inject host metrics where a test is intended to be portable. Recheck the W4 oracle there. This is not evidence of 90 production defects, and it is not a green local gate. [Failure node IDs](evidence/v1-review-20260924/api-failures.txt) and [verification summary](evidence/v1-review-20260924/verification.json) preserve the result.

## 4. Recommended launch order

These are proposed delivery packages, not newly dispatched tasks. Preserve P-17's backend acceptance gate unless the user explicitly changes it. App contract review and integration preparation can be planned now; their release must still consume an accepted backend.

| Order | Deliverable | Existing tasks / added closure | Evidence required to leave the stage |
|---|---|---|---|
| 0 | Establish the release truth and honest public contract | E1B/E4B; RV-01/04/08; P-18/P-22 | Latest run results recovered; one SHA-bound decision; supported model ID, 82s ceiling, input constraints and retention disclosed consistently. |
| 1 | Reliable operated Marlin API | D/M/G/W closure RV-02/03/05/07/11; I3B closure RV-09/10 | Restart-safe accepted work/upload/results, bounded storage, typed overload, cancellation, actual alert delivery and known-good restore/rollback. Complete a real dataset-client run and resume. |
| 2 | Consumer CREDIT service | A1 reuse, approved A3 rate card, G/D cutover, C0/C3A | One-time individual entitlement and actual CREDIT reservation/settlement proven; no USD conversion; scoped keys; exhausted balance/refusal and operator recovery. |
| 3 | Complete the small consumer App | A2/A3/U1R/U2; necessary U3; C0/C3A | Verified onboarding, keys, catalog/quickstart, real usage/balance, failure/result states, privacy/support information. No fixture data or placeholder live claims. |
| 4 | Release the consumer journey | E3A/I2A/I3/E4; staging/email/abuse checks P-05 | Two-tenant end-to-end gate, recovery/accounting drills, published operating limits, monitored gradual onboarding and a tested rollback. |
| 5 | Tune against actual demand; begin Lab when App is accepted | M4/W4 followups, then L1–L4/E3L/I2L and later provider milestones | Representative workloads show the next bottleneck; each optimization is a versioned, measured variant with quality/parity evidence. |

Stages 1–3 have parallelizable work, but share three interfaces: consumer identity/CREDIT, published capability/rates, and result/retention lifecycle. Freeze those changes in one contract lane and serialize migrations before assigning disjoint implementations. A later session can turn this table into worktree briefs after the priority discussion; do not have several workers independently redesign the wallet or upload schema.

A manually provisioned, capped **invited API pilot** can precede self-service while product pages are unfinished, provided its honest scope, operating controls and evidence support it. It must not be presented as the completed public free-plan product. Maximum throughput, autoscaling and multi-host HA are separate measured investments; single-host recovery and explicit capacity bounds are still launch requirements.

## 5. Overall pending work, without mixing it into consumer v1

- **Consumer completion:** A2/A3/C0/C3A/U1R/U2/U3 and E3A/I2A/I3/E4. A1's grant function exists but browser onboarding/CREDIT admission are not joined. Backend closure above must be added to implementation briefs rather than lost under M3/I3B “implemented.”
- **Provider foundation:** L1–L4, I2L/E3L and moving the fixture trace explorer through V1M. Keep ordinary customers' App focused on their requests, usage and credits.
- **Provider observation/review:** T2I/T2F/T3, D6F/D6J, C2/C3F/C3L, V2/V3, G4F/G4T, J2/J3 and E5L. Permissions and actual delivery are prerequisites; spool/judge fakes are not a live feedback loop.
- **Dataset/evaluation loop:** F3/D7/N1–N4/H1/B1–B4/I5/E6L, with provider-owned/authorized data and an agreed SOP rubric (P-07).
- **Improvement/training:** D8/P1–P4/I6/E7L; support manual export/external training/checkpoint import before advertising an automatic connector. Teacher calls need explicit budget and source-purpose permission.
- **Controlled rollout/optimization variants:** D9/R1–R4/I7/E8L, after measured candidates and rollout populations exist.
- **Conditional scope:** G5 callbacks if clients need them; I4 fleet only after measured need; X1–X6 live video, robotics/ROS2 and alternative hardware require their own workload/gate. Speech remains deferred.

The open cost-source P-19 blocks a cost-per-video-hour claim, not correctness work. Missing ground truth P-07 blocks SOP accuracy guarantees, not a clearly described finite-video inference product. P-01 rates, P-05 signup delivery/abuse bounds, serving-data retention and the operational acceptance decision do block public v1.

## 6. Local work disposition

The original `codex/wave2-platform-audit` checkout was dirty and was preserved. Main was fetched/pulled in `/Users/rey/Documents/GitHub/model-inference-v1-review`; audit work used `codex/platform-v1-review-20260924`. Earlier planning commits through `ec6c5483` are already ancestors of main.

The user authorized relevant, conflict-free local work to join main. The small [`smoke.py`](../../models/marlin2b/smoke.py) change was selected: read `MARLIN_API_KEY` with the existing anonymous fallback, and use a monotonic duration clock. Both key cases and stream timing were checked without network access. It remains a **direct-vLLM smoke utility**, not the consumer quickstart: its engine-specific parameters/stream options are not automatically accepted by the gateway.

The uncommitted Modal/OpenInfrx deployment, second-model work, experimental client and benchmark results were preserved in the original checkout. They were not promoted as consumer backend implementation. Their proxy uses Modal credentials, separate model routes/configuration and no shared durable admission/wallet/settlement boundary. A future integration should place a qualified hosting adapter behind the same platform authority and benchmark the exact serving version. The local `--disable-thinking` benchmark option also needs target-specific handling; forwarding it unchanged to the closed gateway parameter set would be invalid.

## 7. Verification in this review

See [audit probes](evidence/v1-review-20260924/reproduce.py) and [their recorded observations](evidence/v1-review-20260924/observations.json). They use synthetic media/auth and in-process/shared in-memory storage. No hosted customer data was accessed. They deliberately document failures at this base; they are not passing release-acceptance tests.

- Frozen Python environment installed successfully.
- `make bench-test`: **68 passed**.
- `python -m pytest -q tests/integration/backend/test_certify.py` with the pinned interpreter: **38 passed**.
- `make console-test console-lint console-typecheck`: **289 tests passed**, lint **0 errors / 2 existing warnings**, route generation and TypeScript check passed.
- Smoke utility: **2 execution probes passed** (environment key/default key and deterministic monotonic durations), without a real engine or SDK network call.
- `make api-test`: **2,988 passed / 90 failed / 640 skipped**, two warnings, 596.08 seconds; nonzero exit. Diagnosed platform/harness dependencies and limits are recorded in RV-12 and the [verification summary](evidence/v1-review-20260924/verification.json). No full-suite pass is claimed.
- Plan validator: **passed** for 119 tasks, dependencies, release-gate closures, ledger and local Markdown links. `git diff --check` passed.
- Docker daemon is unavailable on this Mac; real PostgreSQL/PostgREST/Valkey/object-store fault integration and the GPU release gate were **not rerun**. Prior Linux/hosted evidence is cited as prior evidence only.

## Verification log

- 2026-09-24: Reviewed main `d7dc3690`, entry handoff/operational tail/tracker, relevant gateway/worker/media/database/App/client/release code and results. Reproduced upload protocol and restart gaps; examined collector restart semantics; checked public health/discovery/login without inference. Added this review and entry-point corrections, retaining historical evidence and task statuses. Recommendations await priority discussion; no hosted settings or release decision changed.
- 2026-09-24: Fetched `726d004d` before publishing; incorporated new E1B acceptance evidence and checked the three raw files against their summaries. Rebasing the audit retained the implementation session's changes to handoff 20 and its append-only coordinator record. No runtime code changed in that upstream delta; earlier local test results remain labelled with their tested base.
