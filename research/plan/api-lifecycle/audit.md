# Lifecycle audit: what exists and where the journey stops

Scope: repository code at the source baseline in [README](README.md), plus deployed API checks dated 2026-10-01. Findings below distinguish domain logic, mounted HTTP routes and working production composition. An empty authorized list establishes a successful read, not a successful workflow.

## 1. Provider access and project setup

**Intended:** a provider signs in, selects a workspace, creates a model project and supplies the model/card/weights. The backend explains which serving profiles support the model.

**Current:** Lab membership is read directly using `lab_provider_memberships` from `apps/lab/lib/auth/memberships.ts`. The test account has one developer membership in an empty internal workspace. It does not own NemoStation's existing public model and must not be made its owner merely to get a demo to pass.

`GET /lab/v1/control/{models,deployments,proposals,aggregates}` exists. All four returned **200 with zero records**. There is no FastAPI workspace onboarding/member-management API. The existing registration form asks for a name, one artifact digest, schema and runtime; it does not accept weights or create a standalone model project.

**Needed:** session/workspace APIs; model-project record distinct from revisions; provider ownership and authorized artifact import/upload. See AP-01, AP-04.

## 2. Importing the actual model

**Intended:** upload all shards and auxiliary files, or import a pinned source; verify the manifest, license/rights, checksums and compatibility; obtain an immutable artifact ID.

**Current:** `infrx/lab/control/operations.py:register` searches for a model with weights **already imported by an operator into the same provider**. It only permits a shard digest from that model and inherits deployment limits from an existing revision. It then clones the existing serving record, changes runtime/schema, and creates a private deployment record. Concurrent registrations allocate labels by counting existing rows; there is no durable registration operation or atomic all-or-nothing workflow across these two writes.

We posted the actual Marlin shard digest and pinned runtime from `models/marlin2b/serving-version.json` to the real registration API in the empty internal workspace. It returned **404**; subsequent model/deployment lists remained empty. Source maps the no-imported-model case to `not_found`; the initial probe did not retain the response's `refusal` value. This is a product onboarding gap. We did not upload model bytes.

Existing `/v1/uploads` routes are **consumer video uploads**, not model artifact uploads. There is no model-weight upload/import API. The available runtime allowlist contains `vllm/vllm-openai`; arbitrary images and SGLang are not supported by this flow.

**Needed:** artifact upload/import, manifest verification, model-project creation and revision creation independent of a previously deployed model. A future UI must never instruct a new provider to paste an arbitrary digest and call that uploading a model. AP-04.

## 3. Configure, deploy and connect a private endpoint

**Intended:** choose an approved compute/runtime profile and input/output contract; submit deployment; follow progress; run a smoke; obtain an endpoint URL and endpoint-scoped developer key.

**Current:** domain records, permissions and deployment transitions exist. Actual Marlin hosting is configured through `models/marlin2b/serve.sh` and operational rollout scripts, rather than a provider deployment API. There is no weight-to-GPU provisioning service in the Lab route family.

`infrx/lab/compose.py:lab_operations` supplies **`NoEngine()`**. Its smoke method raises dependency-unavailable. `LabControl.validate` transitions a record to `validating` *before* that call; an exception/interruption leaves it there. We did not deliberately strand a production record to reproduce this source-confirmed gap.

`LabControl.issue_dev_key`, private pricing and provider funding exist as domain/operator methods, but are not exposed by the control HTTP router. A record's coarse `active` field and inferred `smoke` field do not provide engine identity, allocation, progress or a reachable endpoint receipt.

**Needed:** durable deployment operations, a reconciler, real engine adapter, verified endpoint binding, private key APIs and explicit provider budget/configuration. AP-05 and AP-06. Fix stuck validation before enabling the current Smoke action.

## 4. Release into consumer distribution

**Intended:** a provider administrator proposes a qualified revision; an operator reviews the pinned limits/rate/readiness and approves. Consumers discover and invoke exactly that listing. Rollback affects new admissions without changing existing request pins.

**Current:** proposal creation and operator rejection have routes. Approval/pricing/publication exists in domain/CLI code, not a complete HTTP journey. The route schema permits `kind=rollback`, but the operation refuses it: rollback is currently an operator listing decision. The UI should not offer a provider rollback operation that the implementation refuses.

`gateway/routes/models.py` projects **one configured `settings.model_id`**, with a Marlin-specific approved profile and `OWNED_BY='nemostation'`. Adding a model row does not automatically add a consumer catalog item or configure a serving route to another engine.

The deployed catalog returned **200**, one model `nemostation/marlin-2b`, listing version 2, availability `available`, CREDIT rates 400/M input and 1200/M output. Runtime image reference is pinned; the separate `runtime_image_digest` field is null in this response. Keep the distinction between a reference containing a digest and a fully populated readiness/identity receipt; validate coherent pins before future promotion.

**Needed:** audited operator approval/rate/rollback APIs and an atomic publication path connected to qualified endpoint routing and the catalog. Preserve the existing working Marlin listing while adding support. AP-06.

## 5. Consumer account, credits, keys and requests

**Intended:** the user signs in, claims the one-time individual grant, creates a key, chooses a listed model, uploads a clip and runs sync/SSE/async inference. App lists the request, result and final charge using API responses.

**Current inference APIs:** `/v1/models`, `/v1/chat/completions`, `/v1/jobs` with status/result/events/cancel, `/v1/uploads` with byte upload/finalization, and feedback/trace-export route implementations. Existing acceptance, job journal, retries/fences, credit settlement and video handling are valuable foundations. Do not replace them with a second serving pipeline.

**Current web backend:**

| Frontend-owned area | Concrete source | Required migration |
|---|---|---|
| Account and tenant resolution | App `lib/session.ts`, `lib/services/console.ts`, team page | FastAPI resolves current identity/account/capabilities |
| Keys, secret generation/hash, replay cache | App `lib/services/actions.ts`, `lib/keys.ts`, `app/actions.ts` | Server key service with durable replay; web is transport/display only |
| Signup grant | App `app/(auth)/grant.ts` uses an admin client/RPC | Backend campaign grant service; once per verified individual |
| Usage, wallet, ledger, results, cursor construction | App `lib/services/console.ts`, `query.ts`, `cursor.ts`, billing `credit-reads.ts`, usage `request-reads.ts` | Scoped console API reads and backend-issued cursors |
| Operator reads/mutations | App `app/(console)/admin/operator-{reads,port}.ts` | Operator-authenticated FastAPI endpoints backed by existing audited operations |
| Feedback | App `lib/services/feedback.ts` | Session-authorized API over existing domain; page wiring still required |
| Workspace and capabilities | Lab `lib/auth/{guard,memberships}.ts` | Workspace/session API; permission enforcement stays server-side |
| Judge config/budget/run/calibration/list | Lab `lib/services/judge/{core,runs,session}.ts` | Typed Lab judge APIs over existing RPC/domain |
| Request review feedback | Lab `lib/services/review/index.ts` | Authorized trace/review API |

The key creation replay cache is a process-local `Map`; it cannot ensure one key across server restarts/multiple web instances after a lost response. Preserve the existing UI's stable request identity, but move the authoritative uniqueness check to durable storage.

Proposed `/console/v1/me`, `/console/v1/keys` and `/console/v1/credits` each returned **404**. Source inspection corroborates their absence; testing a speculative path alone would not establish that no equivalent API exists. We did not manufacture a consumer key through the database and call it an API success. The unauthenticated inference call returned **401**, as expected.

**Needed:** AP-01–03 and AP-09. Keep the free grant policy, exact decimal arithmetic, suspended-account behavior and existing consumer-scoped visibility.

## 6. Trace capture and provider analysis

**Intended:** consumer-controlled capture and purpose-specific grants; asynchronous shipping; provider-visible operational aggregates and authorized metadata/content; request timeline, revision/harness identity, latency, tokens, credits and content availability.

**Current:** trace spool/shipping/retention, feedback projection, grant-aware trace reads and async-job capture exist in source. `worker/__main__.py` calls `capture.capture_jobs` when `TRACE_PUMPS` is on; async coverage is not wholly missing. However, the deployment inspected has no trace-store configuration in either gateway or Lab control container. Only the inference worker and Lab control were running; no Lab judge/evaluation worker was running.

`/lab/v1/traces` returned **404**. The separate Lab control composition mounts it only when trace storage is configured. An absent route must not appear as an empty healthy request list. Existing carried row 26 requires real trace-pump delivery/restart proof; row 35 concerns trace-export composition. Preserve those gates.

The consumer's current settings page is informational; there is no complete FastAPI flow for choosing capture/sharing/judge purposes. Trace off does not erase serving media/results. An administrator of a provider workspace does not gain permission to read customer clips or grant their use.

**Needed:** AP-07: grant/capture APIs, deployment composition, reliable sync/SSE/async tracing, availability and API projections for request detail. Explicitly show `not captured`, `metadata only`, `content expired`, `permission revoked`, and `service unavailable` as different states.

## 7. LLM as judge, human review and model improvement

**Intended:** inspect a request, choose a versioned rubric and judge, estimate provider spend, run within budget and granted purposes; see criterion results, evidence, abstentions and calibration. Review creates separate human labels. Failure datasets drive benchmarks and future model revisions.

**Current:** judge/feedback permissions, budget and run domain/RPC machinery exists; Lab invokes it directly through Supabase. No equivalent FastAPI judge family is mounted. `/lab/v1/judge/runs` returned **404**. The judge worker is not running; no real judge inference was attempted. Source defaults external judging to dry run unless explicitly configured and permitted.

Dataset version listing returned **200 empty**. Evaluation catalog/runs/experiments/subscriptions and pipeline training-runs/checkpoints/teacher-batches each returned **503**. Release/optimization lists returned **200 empty**. Source maps these service failures to `unavailable`; the initial probe retained statuses, not the `refusal` values. These prove partial composition, not a functioning data/training loop. Evaluation killed-attempt double-debit risk (carried row 27) remains an enablement gate. External training still requires a selected provider/adapter; do not relabel checkpoint import as hosted training.

**Needed:** AP-08/10. Online request judging, offline benchmark evaluation and teacher annotation batches are different workflows. SOP correctness requires actual video/evidence plus the SOP definition and reviewed labels; response text alone cannot establish whether the robot performed a step. No judge score is a universal production-quality guarantee.

## 8. API contracts and actual availability

Both FastAPI factories disable public docs/OpenAPI URLs. That is not itself a bug. But many handlers manually parse `Request` and return generic JSON responses, so generating `.openapi()` alone will not document their real request/response types. Add explicit DTOs, errors, auth, operation IDs and checked schema artifacts before generating clients.

Consumer `/health` returned **200**. Public `/readyz` and Lab `/health`/`readyz` returned **404**. The Lab edge intentionally only proxies `/lab/v1/*`; these public 404s do **not** show that internal service readiness is down. Add an authenticated product-level availability contract rather than exposing operator health details or guessing from arbitrary routes.

## Priority and limits of this audit

P0 for the **requested complete lifecycle**: first artifact/model import, real private deployment, operator publication/routing, console/key APIs, consented trace composition, judge APIs/worker and real acceptance evidence. These are not all prerequisites for an invited consumer using the already provisioned Marlin endpoint; that narrower gate remains in STATUS/review 26.

No schema, engine, feature switch, price, provider ownership or customer permission was changed. The test account's empty-workspace registration was refused. API evidence excludes session tokens, passwords, private content and provider IDs. Existing source tests passed in the in-memory mode; PostgreSQL/live engine/judge workflow tests remain to be executed after their prerequisites.
