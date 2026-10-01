# API-only lifecycle acceptance and implementation handoff

## Executed audit versus remaining acceptance

The dated [probe report](evidence/deployed-api-probe.json) contains 33 actual HTTP observations. [Host evidence](evidence/host-observations.json) records the active E4C container and missing trace configuration. Source-level control/auth tests passed (33; 18 PostgreSQL cases deliberately not run). These results verify the gaps; **they are not an upload/deploy/infer/judge pass**.

The current environment cannot complete that pass: first-model artifact APIs, real deployment smoke, operator publication, console key APIs and judge APIs are missing; trace composition is off; relevant evaluation/pipeline reads return 503. The single GPU also had an active certification run. Do not bypass missing endpoints through SQL/CLI and present the result as API coverage.

Reproduce the bounded probe using the API Python environment and an existing session token file. Token and membership files must be owner-readable only, outside the repository. The membership bootstrap currently uses the legacy RPC; record it as a gap. The runner never requests a password, prints tokens, creates a key, launches a deployment or submits authenticated inference.

```sh
apps/infrx-api/.venv/bin/python research/plan/api-lifecycle/probe.py \
  --token-file /secure/task/session.token \
  --membership-file /secure/task/selected-membership.json \
  --check-registration \
  --output /secure/task/deployed-api-probe.json
```

The optional registration check runs only after a 200 exact empty model list and uses actual Marlin pins. It is a real mutation request expected to refuse in the current empty workspace; it is not appropriate as a generic read-only health probe once onboarding semantics change. Re-read the code before repeating against a later release. Run without `--check-registration` for read-only product checks.

## Prerequisites for the real lifecycle runner (AP-11)

1. Record API/App/Lab/controller/worker image and source identities, applied schema range, approved runtime profile and generated API schema digest. Read fresh STATUS and the active E4C result. Do not assume source main equals every running service.
2. Use a dedicated test provider, administrator/developer/viewer identities, operator session, consumer A and consumer B. Provision identities through the supported auth/operator APIs. External initial operator credentials may be an explicitly documented environment prerequisite; product rows, keys, grants or artifacts may not be seeded behind the runner's back.
3. Choose task-owned prefixes, ports, model slug and candidate endpoint. The initial hardware policy is **zero extra GPU allocations by default**. The runner requires an explicitly configured isolated target/profile and maximum resource budget before creating one. It must not replace or overload the serving Marlin process or overlap E4C.
4. Configure a model import source/manifest that the test provider is entitled to use; known fixture video, SOP definition and human-reviewed reference labels. Synthetic video tests transport, not robotics task accuracy. Actual task-quality tests use an approved representative SOP dataset.
5. Configure trace storage/retention and an approved media-capable judge, secret reference, payer and conservative maximum spend. Grant external egress for test-owned data only. No configured judge means BLOCKED for live judging; dry-run can test planning only.
6. Fix accounting/worker gates before enabling them. Rehearse migrations and service rollout using existing operating runbooks. Backups, isolated credentials and resource cleanup remain coordinator responsibilities.

No new generic user-confirmation step is added by this document. These are concrete technical prerequisites and existing operator/domain authority checks, not a reason to re-ask for authorization already given.

## Runner design

Build `tests/integration/api_lifecycle/` with a serial orchestrator over generated/schema-validated HTTP requests. Reuse the existing task-local environment and test services. All product operations go through FastAPI. A control-only run can use deterministic fixtures, but must declare them and cannot satisfy the real GPU/real judge gate.

Persist a **0600 task-local state file** containing resource/operation IDs and request hashes; keep credentials in a separate secret store/file and never embed them in reports. At every stage checkpoint, record response identity and verify invariants. On restart, GET the existing operation and reconcile before retrying its original idempotency key. Do not allocate a new deployment, submit another inference or send a second judge request merely because the client timed out.

Modes: `inspect` (read only), `isolated` (task-local real services/controlled engine, explicitly limited proof), `live` (real approved model and judge), `cleanup` (only state-file-owned test resources). No live mode without explicit config naming origins, identities, target, budget and maximum requests. Default maximum live inference requests should be small (for example six accepted requests for sync/SSE/async and replay checks), not a load test. A separate scale test uses the predeclared certification profiles and acceptance thresholds.

Exit codes: 0 only when every required selected stage passed; 1 assertion failure; 2 unmet prerequisite/BLOCKED; 3 interrupted/unknown operation outcome needing resume. Optional stages are explicitly marked NOT RUN and cannot be included in a “complete lifecycle” claim. Keep the existing bounded audit script's separate semantics clear.

## Exact positive-path sequence and assertions

| # | API-driven step | What must be proved before proceeding |
|---|---|---|
| 01 | Auth/session APIs for each actor; workspace/account/capability reads | Correct roles and distinct audiences; fresh feature availability; no unauthorized membership/grants |
| 02 | POST model project; start pinned import or upload parts/complete | Empty workspace succeeds; manifest hashes cover all actual model files; operation completes once |
| 03 | GET verified artifact; create immutable serving revision | Actual source commit, model/card/schema/harness/processor/runtime pins, supported hardware profile |
| 04 | POST private deployment; poll its operation and detail | Isolated resource allocated; real engine reports matching identity; state is not inferred from record presence |
| 05 | Operator private rate/funding API; bounded smoke; issue provider-dev key | Approved private CREDIT meter; exact readiness receipt; key audience/endpoint scope correct; no public exposure |
| 06 | Call private endpoint with finite-video fixture | Real media processing and response; charge/usage receipt; no text-only substitute for modality smoke |
| 07 | Provider administrator proposes; operator approves through API | Expected listing version, approved rates/limits and candidate receipts validated; decision audited |
| 08 | GET public catalog; GET account; claim grant; create consumer A key | Newly published test listing appears with truthful contract; one individual grant; one key despite retried creation |
| 09 | Allocate consumer upload; PUT bytes; complete; submit async video job | Upload not mistaken for model artifact; stable idempotency; result references the requested published serving version |
| 10 | Poll status/result and console request/ledger APIs | Terminal result and one exact CREDIT settlement; reserve released correctly; request/deployment/listing/rate pins agree |
| 11 | Repeat bounded sync and SSE calls to cover supported modes | Correct sync behavior, stream journal/disconnect rules; no implicit async conversion; exact usage/settlement per request |
| 12 | Enable capture and scoped sharing/external_judging grants for test key/data; submit one captured request | Permission persisted through API; prior uncaptured request stays uncaptured; use remaining request budget |
| 13 | Poll Lab trace list/detail for that request; inspect authorized media | Same request ID/pins; real capture content/timing/usage; metadata-only and unavailable content distinguished |
| 14 | Create rubric/config; estimate; set budget; POST judge run; poll results | Approved live adapter invoked once; video/SOP evidence available; real judge result or honest abstention; exact provider spend |
| 15 | Read consumer credits and provider budget after judge | Consumer not charged for provider judging; run replay does not duplicate spend; estimates separate from settled cost |
| 16 | Submit human review; read review/calibration | Immutable reviewed provenance; inadequate reference sample shows insufficient calibration |
| 17 | Revoke sharing/judging grant; retry reads and enqueue a follow-up judge | Future prohibited content read/egress refused; no permanent content bypass; aggregate access follows its separate policy |
| 18 | Roll back/retire test listing and candidate through operator/control APIs | Prior jobs keep original pins; new admissions use correct listing or refuse; task-owned resources drained and cleaned |

For the complete improvement-loop extension: derive/import a permitted dataset with held-out split → run baseline/candidate experiment → read report and compare on the same cases → export approved training bundle/use supported external provider → ingest checkpoint → create new immutable revision → repeat readiness/benchmark/publication gates. No fine-tuning run is claimed if the external trainer was not executed.

There must be an explicit fixture-grant plan for step 17 if dataset creation is run later: use a separately authorized test cohort or execute the improvement extension before revocation. Do not silently re-enable a revoked grant to make a later stage pass.

## Required failure tests

| Boundary | Injection | Oracle |
|---|---|---|
| Identity | Expired token, forged provider, wrong audience, revoked member | 401/403/404 per policy; no side effect or hidden record disclosure |
| Artifact | Wrong shard hash, interrupted part, duplicate complete, mutable source, foreign object | No verified artifact from unverified bytes; replay stable; safe cleanup |
| Deployment | Kill controller after allocation/before receipt; failed image load; OOM; smoke timeout | Exactly one resource, stale fence refused, terminal/reconcilable state, no public readiness |
| Private endpoint | Consumer key, another provider's dev key, unpriced ready revision | Refused with no wrong-wallet debit or hidden endpoint leak |
| Publication | Concurrent approval/rollback, stale expected version, stale smoke receipt | One listing winner; no unready/unpriced public model; active job pins unchanged |
| Key/grant | Lost successful response; repeated claim across web/API instances | One key record / one campaign grant; safe one-time-secret recovery UX |
| Inference | Same idempotency key/body and changed body; disconnect; transient overload; worker kill | Existing durable protocols hold: replay or conflict, no duplicate committed output/settlement |
| Tenant visibility | Consumer B reads A request/result/upload; provider guesses unshared request | No foreign content; keys/results remain properly scoped |
| Trace | Storage down, lost acknowledgement, duplicate delivery, capture off, expiry | Serving continues; eventual one logical trace when configured; no fabricated/retained-forbidden content |
| Judge | Permission revoked before send; missing video; budget race; timeout after provider accepted; worker kill | No unauthorized egress; abstention for missing evidence; bounded spend; ambiguous send quarantined, no blind retry |
| Evaluation | Killed attempt before/after usage receipt and settlement | No double debit; reconcile released holds using current authoritative oracle |
| UI boundary | API failure; direct legacy RPC fallback; stale capabilities | Clear unavailable/error; zero product database calls; no false success/zero metrics |

Use existing real-process fault suites for these behaviors where they already cover the same seam. Extend them only for missing API/lifecycle joins; do not mirror implementation in hundreds of shallow DTO tests. A source/mock pass cannot replace a real serving route, worker restart or hosted policy test.

## Evidence and release decisions

Each stage emits: start/end UTC, method/route template, HTTP status, correlation/operation/resource IDs, assertions, exact source/image/schema/runtime/dataset/rubric versions, resource/request/spend counters and safe cleanup outcome. Store private identifiers/content only in protected test storage; repository evidence uses redacted references/hashes. Never include passwords, session/API keys, DSNs, signed object URLs or raw customer media.

Report four verdicts independently:

1. **API boundary:** every shipped action documented/backed; no product DB logic in frontend.
2. **Lifecycle:** real artifact → engine → consumer → authorized trace → real judge completed.
3. **Quality:** measured SOP/model/judge performance on a defined reference set; smoke tests do not establish this.
4. **Operations/performance:** existing certification/scale/recovery gates; small lifecycle checks do not establish production throughput or Modal cost parity.

Update STATUS and the existing task/register evidence only when these individual outcomes are established. Do not change thresholds after seeing failures. No “all green” headline when any required stage is BLOCKED, skipped, simulated or dry-run.

## Fresh implementation session prompt

Copy the following into the implementation session, adjusting only the checkout/branch details after fetching current main:

> Implement the API-first Marlin lifecycle described in `research/plan/api-lifecycle/README.md`. First read `STATUS.md`, `CLAUDE.md`, `research/plan/README.md`, this package's audit/contracts/implementation/verification files and the existing carried-work register. Inspect current main and deployed release identities; do not assume the dated audit is the latest operating state. Keep any newer completed work and prove it rather than rebuilding it.
>
> The required product flow is provider model project/card + verified weights → private qualified vLLM deployment → endpoint-scoped dev key and real finite-video smoke → operator-approved publication and consumer catalog → verified individual grant/key/inference/result/credits → consumer-consented Lab trace → bounded media-aware LLM judge → human review. Extend into dataset/evaluation/revision promotion only after its accounting and worker gates pass. Existing Marlin is finite video/text to text; it is not live streaming or robot action output.
>
> Both apps must become extremely thin clients of structured FastAPI APIs. Preserve only presentation and session/cookie transport in Next.js. Move account/membership/product queries, keys, grant claims, billing projections, operator actions, consent and judge/review RPCs behind FastAPI. Reuse existing domain/SQL logic and preserve authorization, accounting and idempotency; do not install broad service credentials in the web apps. Add explicit OpenAPI request/response/error/auth schemas and generated clients. Every visible action needs a documented working API or an honest unavailable state.
>
> Reconcile AP-00 through AP-11 into the existing task manifest and carried register. Record a committed base, path ownership, dependencies, environment and testable exit before dispatching work. Use isolated worktrees and independent work packages for parallel work; one coordinator owns migrations, shared contracts, composition, generated clients and deployments. Existing UI design lanes must obey this API boundary, and the first-model UI must use the new import flow rather than the old revision-only Register form.
>
> Preserve the current production endpoint and active certification window. Do not enable unresolved evaluation accounting, trace pumps or external judging without their concrete composition and real-process proofs. No arbitrary GPU allocation, customer-data grant or public listing replacement to make a demo pass. Use the bounded test identities, owned media, isolated target and configured resource/judge budgets in verification.md.
>
> Build the resumable API-only runner, then execute it against isolated real services and the approved live target. No direct product SQL/CLI/Supabase-RPC shortcuts count as an API lifecycle pass. Record true failures, blocked stages, costs, identities and cleanup. Run the relevant existing regression, fault and scale suites, and deploy with the existing runbooks only after their gates. The current probe.py is a gap audit, not the acceptance runner.
>
> Finish by reporting API-boundary, real-lifecycle, SOP-quality and operating/performance verdicts separately, updating STATUS/docs and the existing tracker with evidence and remaining blockers. Never label a mocked engine, dry-run judge, empty listing, health response or successful build as end-to-end production completion.
