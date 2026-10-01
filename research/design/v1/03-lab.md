# Provider Lab experience specification

Goal: a model engineer understands what is registered, what is actually serving, what evidence exists and what action is safe next. The initial workload is Marlin finite-video understanding for SOP analysis over recorded robotics datasets. It is not a robot control console or live sensor stream.

Use [foundations](02-foundations.md), [service boundaries](05-service-contracts.md) and the [screen reference](design-reference.html). Phase labels below are implementation bands, not claims that services are enabled.

## Phase L1: usable provider operations

### L-01 — Sign-in and workspace access

Route: existing `/sign-in` and provider layout access states. Center a 400px card with “infrx Lab”, “Manage and improve your models”, email/password and Sign in. Explain that access requires provider membership. Offer “Looking for an inference endpoint? Open the App.” Recovery must use a verified supported auth route; do not link to a guessed Lab reset page.

Labels stay above fields; email autocomplete=username, password=current-password. Submit shows “Signing in…” and cannot double-submit. Generic credential failure does not disclose account existence. Provider access unavailable is a separate retryable state, never “wrong password.” Password is never restored into HTML or telemetry after failure.

For multiple workspaces, show a searchable list only when the list size warrants search. Each row: name, role, “Open workspace.” Persist only a preference; recheck membership server-side every time. A workspace change clears in-memory content, resets scoped filters and resolves the destination again. If membership disappears, stop requests and show access denied.

No workspace: explanatory card with App link, Sign out and “Ask your provider administrator for access.” No self-serve invitation or organization creation without its own backend contract.

**Acceptance:** signed-out, single/multiple/no membership and failed membership lookup preserve distinct states; forged workspace selections remain rejected. Keyboard and mobile work without inspecting source.

### L-02 — Overview

Header: workspace name + “Overview”; subtitle “Your models, deployments and next steps.” Developer primary action “Add model”; viewer sees read-only explanation. First-time content is a three-stage setup list:

1. Add a model — create its project, import/upload and verify its artifact, then create a serving revision. This requires the [artifact API work](../../plan/api-lifecycle/contracts.md#4-artifact-and-model-project-onboarding); until available, show the actual setup prerequisite, not a form that cannot succeed.
2. Verify a private deployment — only an actually supported operator/engine workflow can complete it.
3. Request publication — administrator proposes, operator decides.

Stages derive from authoritative records, not local click tracking. With existing ports, stage 2 may say “Serving readiness not available in this view” and remain incomplete. A passed generic smoke cannot complete real-engine qualification.

Once records exist: compact counts for registered deployment records, public records and pending proposals, each linking to the corresponding list. Below: “Recent measured traffic”, with observation window, requests, errors/error rate and p95 if provided. Missing p95 shows “Not available”; zero samples show “No measured requests.” No synthetic trends, uptime percentages, throughput or GPU utilization.

Each service section loads/fails independently. Control service unavailable must not clear a successfully fetched aggregate panel, and vice versa. “Last loaded” is the UI fetch time; “Observed through” is an evidence timestamp and must not be confused with it.

**Acceptance:** empty workspace gives one obvious next action; partially failed reads retain page identity and successful data; viewer cannot register via UI or direct action.

### L-03 — Models and registration

Route `/models`. Header action “Add model” follows the project/import/validation/setup flow in the [API lifecycle specification](../../plan/api-lifecycle/contracts.md). The existing four-field form below becomes **New revision of an imported model**, available only for a model already imported into this workspace with established limits. In an empty workspace it cannot succeed; never present it as uploading weights.

List columns: model name, revision, runtime, registered date, details. Artifact/schema values live in expandable row detail rather than dominant columns. Display names may be derived from the registered name; no new editable model title is assumed. Search across the loaded list must say it searches loaded records until server search exists.

Registration opens an in-page form with a clear Back link (or new `/models/new` route if added by the owner), max width 720px. Two steps plus summary:

| Step | Field | Behavior |
|---|---|---|
| Identify | Model name | Persistent machine name; existing lowercase/digit/hyphen rule, up to 63 characters, preview its intended identifier |
| Revision | Artifact SHA-256 | Explain how to obtain the digest of the approved artifact; validate exact existing digest syntax, preserve value on errors |
| Revision | Schema version | Existing identifier grammar; known approved options only if a catalog exists, otherwise labeled advanced identifier input |
| Revision | Runtime | Existing identifier grammar; this identifies a runtime record, not a hardware reservation or automatic engine selection |
| Review | Read-only summary | Show all four values; final button “Register private revision”; no GPU price estimate or endpoint promise |

Do not add model-card, HF token, weights upload, hardware or scaling fields to this legacy registration payload. They belong to the new typed import/deployment APIs and Add model flow. Credentials use backend secret references; no raw token in metadata or browser-persisted drafts.

Server validation mirrors existing rules; client validation is convenience only. Return structured field/form failures rather than discarding input on redirect. Success renders the actual model/revision and any returned deployment record, with “View deployments.” If registration's mutation is not idempotent today, add a stable operation receipt before introducing automatic retries; otherwise say “Check models before trying again” on an uncertain response.

**Acceptance:** invalid digest/name handled in place; duplicate/lost-response behavior documented and tested; registering produces exactly the backend's record and no “Live” claim.

### L-04 — Deployments

Route `/deployments`. Default list includes Model + revision, environment/visibility, **record state**, smoke result, next action. Existing `active` is labeled “Registered · active record”; `retired` is “Retired.” Keep `dev/private` and `prod/public` explicit. Do not rename an active record to “Healthy.”

Empty state links to Add model or an imported model's New revision action, according to API prerequisites. Row expansion shows deployment revision ID, serving version, runtime, schema and rate card; full values copyable. A detail route requires CX-02's scoped read before shipping a deep link that cannot resolve independently.

Detail layout: model/revision header; left region “Readiness and checks”; right region immutable configuration. Readiness stages: record registered, engine evidence available, private smoke passed on this engine/revision, publication requested, operator decision. Show only stages supported by evidence; missing stages say “Not verified here.” A recorded control smoke result is still shown as “Recorded smoke result,” with an explanation when its engine provenance is absent.

Actions:

- Developer/admin: “Run dev smoke” only with a configured real adapter and API-confirmed eligibility; show its durable operation/receipt. Current `NoEngine` composition must show unavailable: calling it can strand a record in validating. AP-05 must fix reconciliation before enabling the action.
- Administrator: “Request publication” with confirmation summary of exact revision, environment and current evidence. Explain operator approval. Success = “Publication requested,” never “Published.”
- Viewer/developer without publication capability: show the required role and retain read access. Operator approval stays outside provider UI.
- Approved proposal with no serving health proof: show both facts separately. Request history never substitutes for current readiness.

“Connect” with an endpoint/snippet appears only when server data supplies an authorized endpoint and a verified serving identity. Existing port lacks this proof; v1 fallback is “Connection details are provided after serving setup is verified.” No guessed URL derived from the model name.

### L-05 — Requests

Route `/requests`; existing request detail route retained. This is provider observability, not a customer billing ledger. Heading explains “Requests on this workspace’s deployments.” Default table: request ID, started time, model/revision, mode, content-access label and loss reason if present. Do not invent cost, latency or success columns absent from the trace port.

Existing port supports a cursor and limit only. V1 supplies next/previous page context and exact visible IDs. A server-backed time/model/status filter is CX-03; do not implement a current-page filter that pretends to search all traffic. Empty says “No requests returned for this workspace”; unavailable says “We couldn’t load requests” and offers retry. Unconfigured reason requires trusted configuration evidence.

Detail: breadcrumb → metadata summary → authorized content region → existing feedback/judge/review actions when individually allowed. Show model/revision/serving identity and request ID prominently for debugging. Customer-content access state is independent of provider role:

| Access | Presentation |
|---|---|
| Metadata only | Visible metadata, “Content isn’t shared with this workspace”; no request body fetch, video thumbnail or prompt preview |
| Shared and readable | Authorized fields only; content completeness/expiry and provenance clearly visible |
| Partial content | “Partial capture”; mark missing portions, do not imply full video context |
| Expired/revoked | Clear content from memory and screen; retain allowed metadata; do not offer a bypass via export/judge |
| Unknown/unavailable | Retry read, no stale content rendered as currently authorized |

Do not add bulk “Use for training” or grant-changing controls. A reviewer judgment is a separate record, never a silent mutation to the source trace. Rendering model output as text must not execute arbitrary HTML or links from it.

### L-06 — Settings

Sections: Workspace, Your access, Data permissions, Service availability. Show readable role descriptions and capabilities; state that customer-content permission is separate. Service availability distinguishes configured, unavailable and not yet verified; it is not an uptime monitor. Only CX-01 can provide authoritative setup reasons.

No fake member invitation buttons, API-key management, hardware quotas or consumer CREDIT wallet. If provider-member management gets integrated later, its permissions/audit behavior must be preserved and reverified; this design does not implement it by exposing a button.

## Phase L2: dataset and comparison workflows

L2 can be implemented against explicit fixtures while backend gates close. Production actions stay disabled until their specific integration/accounting gates pass. The Lab must remain useful for L1 if L2 is unavailable.

### L-07 — Dataset library and guided import

Route `/datasets`. List dataset ref/version, derivation, sample count; names/task descriptions require CX-04 metadata rather than invented labels. Primary action Import dataset. A dataset is an immutable version, not an editable spreadsheet. Detail shows lineage, splits, sample references, restrictions, imports and export receipts.

Import steps: **Source → Mapping → Validate → Import**.

1. Source: select supported JSONL file; show selected filename/size locally, supported format and a downloadable schema example. Explain that video references, labels and provenance are data fields; this is not a new multi-GB media uploader. Enforce existing backend/upload size constraints; no new limit invented in UI.
2. Mapping: readable controls for the documented import specification, generated from the actual supported schema. Preserve advanced JSON editor as a secondary mode. Switching modes must be lossless for supported fields or explain unsupported advanced values. Rights/source fields must remain required where backend requires them; never auto-authorize data use.
3. Validate: call existing preview; show mapped rows, rejected lines and causes. Explicitly say preview covers the supplied head/sample, **not a full-file pass**. Editing source/mapping invalidates the preview. User chooses strict rejection or, only if supported, “Import valid rows and report rejected rows.”
4. Import: final summary and one submission. Import job page shows running/published/rejected/failed; “published” here means dataset version created, not public visibility. Render accepted count, rejected lines, dataset/source refs and downloadable safe rejection report. Failed import “Retry as new import” uses `requeue`, preserving failed history.

Until a structured mapping implementation covers the real schema, retain JSON as “Advanced mapping”; do not reduce the backend schema to an invented prompt/completion pair. Preview is not proof of consent or leakage safety.

Derive: choose base/additions by actual version refs; enter train/validation percentages with exact hundredth-percent increments converted to integer basis points. Holdout = remaining share; show all three before submit and validate sum ≤100%. Seed visible and repeatable. Generate dataset ID once per new operation; version resolution must use a verified backend allocation/read, or retain an explicit validated version field. Do not silently guess “latest + 1.” Conflicts keep inputs and require re-read. Leakage report shows overlapping sample/split refs and blocks publication as backend dictates.

Export: format/scope/expiry summary, omitted sample counts with reasons, immutable version and export ID. Never export holdout or training-disallowed data through a simplified “Download all.” Keep already enforced export/grant checks.

### L-08 — Evaluations

Route `/evaluations`; tabs Experiments / Runs / Checkpoint subscriptions. Start at comparisons, not four raw forms. Each experiment row: baseline, candidate, frozen dataset, run states, decision and created time. No result yet = “Running” or “Awaiting comparison,” not zero accuracy.

Create comparison steps:

1. Choose frozen dataset, harness, evaluator from `catalog` options, showing readable label and immutable ref.
2. Choose baseline and candidate serving revisions; show what differs. Unknown differences are not called single-factor. No mutable “latest” ref.
3. Declare protocol: metric source, confidence, non-inferiority margin, minimum cases, required slices. These are expert inputs with explanations, **no platform-invented default scientific threshold**. Seed and max cases explicit.
4. Set exact run limit and unit, review both runs and protocol, submit stable experiment ID. State exactly whether the backend's limit is per run or aggregate after verifying the launch implementation; do not mislabel exposure.

Comparison result: decision first (Accept / Reject / Inconclusive **under this protocol**, never general certification); reasons second; paired counts and missing/error/not-comparable counts; overall and slice intervals; cost by unit; latency sample counts/percentiles; frozen protocol, dataset and report digests in Details. Show teacher judgment as a source, not ground truth. Insufficient cases remain distinct from measured inferiority. Performance comparisons require comparable workload/hardware identity; otherwise label that limitation.

Cancel acts on the chosen run ID and shows the server response. Subscription setup selects external run, frozen suite, per-run and total limits, maximum active and latest-only/every policy. Ledger lists checkpoint received/validated/rejected/evaluated and queued/skipped reasons. Never hide skipped checkpoints as “complete.”

### L-09 — Review and annotations

Route `/annotations`, navigation label Review. Select a dataset/rubric, then queue → sample → decision. Side-by-side region: authorized source/context, proposed labels with method/provenance, and reviewer controls. For SOP work, show timestamps only when the sample's actual schema supplies them. Do not fabricate synchronized video playback from references lacking a media access contract (CX-05).

Actions: accept/reject/correct using existing review contract. Show submitted/accepted/rejected/superseded distinctly. Synthetic/imported/human are provenance values, not quality scores. Only a verified human review may make the backend's permitted ground-truth transition. Reviewer assignment requires administrator capability and a real member catalog; absent one, keep validated advanced ID input rather than arbitrary fabricated users.

Keyboard: next/previous sample and submit controls; optional shortcuts disclosed and inactive while typing. Unsaved correction warns before navigation. Conflict/revocation stops submission and reloads permitted state. Batch actions deferred until per-item outcomes/idempotency exist.

### L-10 — Teacher batches and external training

Route `/training`; tabs Teacher annotation / Training runs / Checkpoints. Explain external training vs infrx inference. No claim that this console provisions trainers or implements RLHF automatically.

Teacher workflow: dataset → rubric/prompt/teacher identity → payer and exact PROVIDER_USD budget → dry-run plan → administrator approval. Show holdout/not-permitted exclusions, ceiling/price version, budget status and batch chunks. Dry run sends nothing; only the approved backend step may send. On ambiguous chunk outcome, resume/query existing identity rather than retry as a new paid batch.

Training workflow: permitted dataset export → objective (SFT or preference) → adaptation (full or LoRA) → base model → payer/budget → prepare manual bundle. Download bundle for external training. Show prepared/submitting/submitted/ambiguous/completed/failed/cancelled honestly. “Ambiguous” offers lookup/reconcile, not Submit again. Reported unknown cost remains unknown; CREDIT is never converted to PROVIDER_USD.

Checkpoint import: external run, checkpoint ID, artifact key and SHA-256; show receipt then existing approval/qualification steps. A received checkpoint is not a deployable model. Link evaluated/qualified serving revisions only after backend evidence exists.

### L-11 — Releases and optimization comparisons

Route `/releases`; Optimizations linked as a subordinate comparison view. Release detail compares baseline/candidates, mode, cohort, weights, frozen plan, observed window, assignments, quality coverage, spend by unit and verdict reasons. Null progress is “No measurement yet” or a specific refusal, never zero traffic. Keep observed p99 distinct from a configured threshold.

Admin proposes Expand/Rollback against the fence revision loaded by the server; do not expose an editable fence field. Stale fence conflict explains a newer policy exists and requires review again. Operator approval remains separate. No draggable traffic slider that implies immediate deployment.

Optimization record: base and variant identities (engine/version/hardware/quantization/capabilities), measured comparison with sources, report digest and equivalent/not-equivalent/inconclusive/rejected. Missing older identity explicitly “Not recorded.” Savings/throughput ratio only from returned evidence; no Modal parity claim from a fixture.

## Deliberately later

Self-serve weights/model-card onboarding, engine/hardware recommendation, GPU autoscaling, scale-to-zero, live robotics/video streams, trainer provisioning and multimodal timeline tooling require separate backend product contracts. This design leaves navigation space for them without shipping inactive controls that suggest availability.
