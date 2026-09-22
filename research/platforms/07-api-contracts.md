# API, model and endpoint contracts

This is the product-facing contract direction. The [durable contracts](../plan/01-contracts.md) continue to specify admission, persistence, streaming, limits and failure semantics.

## Compatibility policy

Use OpenAI-style authentication, model identifiers, chat messages, response/chunk shapes, usage fields and error envelopes where supported. Advertise a tested subset per model. OpenAI's own reference has model-dependent behavior; matching route names alone does not establish compatibility. Pin SDK fixtures and run them against the actual gateway. [OpenAI Chat API reference](https://developers.openai.com/api/reference/resources/chat).

| Surface | Initial behavior | Boundary |
|---|---|---|
| `/v1/models` | Standard-style model list of published, accessible models | Catalog details/capabilities/rates available through a documented extension; never list private dev artifacts |
| `/v1/chat/completions` | Validated text/video subset, sync by default, supported SSE | Unsupported tools/structured outputs/modalities fail explicitly; no silent coercion |
| `/v1/jobs` and job status/result/events | Explicit async extension; `Prefer: respond-async` also supported as already planned | These routes are platform extensions, not claims of OpenAI API parity |
| Upload and feedback routes | Existing owned-upload/feedback contracts | Platform extensions; don't claim compatibility with unrelated provider upload protocols |
| Provider control API | Proposed versioned `/platform/v1/…` model, version, deployment and evaluation resources | Separate audience/permissions; not part of the public inference API; exact routes frozen by L2/L3 |
| Live video / robotics sessions | Future dedicated transport and schema | Do not encode action arrays and deadlines as chat messages merely to preserve a familiar URL |

Public inference keys and provider control credentials are different token audiences/scopes. A consumer key cannot publish a model. A provider control credential cannot spend a consumer wallet. Lab-issued preview credentials are environment/endpoint scoped with an explicit provider budget.

OpenAI-compatible token usage remains token usage. Expose credit consumption, rate version, serving revision and request identity in documented extension fields/headers or the owned usage resource; never place credit quantities into token fields. Never expose internal USD costs as if they were consumer charges.

## Canonical model and deployment definitions

A Model is a provider-owned identity. A ModelVersion records immutable base artifact, adapter, tokenizer and processor digests. A ServingVersion adds preprocessing profile, prompt/harness reference, runtime image, validated engine options, precision/quantization and hardware compatibility. Optimization can create a new ServingVersion without pretending it is newly trained weights.

Endpoint is a stable dev/prod logical name with visibility and allowed caller scopes. DeploymentRevision pins a ServingVersion, validated limits, routing config and environment. CatalogListing references an approved public production deployment and rate card. Keep a serving revision and a rate revision independently identifiable.

Minimum capability record:

```json
{
  "schema_version": 2,
  "api_family": "chat_completions",
  "input_modalities": ["text", "video"],
  "output_modalities": ["text"],
  "stream_output": true,
  "stream_input": false,
  "tools": false,
  "structured_output": false,
  "input_schema_ref": "versioned-schema-id",
  "output_schema_ref": "versioned-schema-id",
  "preprocessing_profile_ref": "versioned-profile-id",
  "billing_meter": "tokens-v1"
}
```

This is an illustrative fixture, not verified live Marlin capabilities. Actual context/output/media limits come from the validated runtime contract. Provider-configured JSON Schemas must be bounded and validated against the supported adapter. They do not permit arbitrary code, undeclared network calls, unrestricted tool execution or custom billing logic.

## Resolution and changes

At admission resolve `model → deployment_revision → serving_version + rate_card_version + policy_version`. Persist those identities with the job before dispatch. Consumer model pins remain stable; aliases may move only through explicit publication/rollback. In-flight work never changes revision during promotion. Retries use the original version and rates.

Endpoint transitions: draft → validating → ready/private → proposed public → approved/active → draining/retired. A failed validation never becomes public. Dev/prod is a deployment property, not a free-text URL convention. A dev key cannot access prod without an explicit entitlement. Production rollout must identify how prior clients opt into or remain pinned across changes.

To avoid blocking App on Lab, operators can create the same minimal version/endpoint/listing/rate records through audited setup tooling. Lab later adds provider workflows around those records instead of inventing a second registry.

## Reproducible evaluation contract

EvaluationRun pins dataset manifest/hash, split, candidate ServingVersion, baseline reference, harness/tool environment, evaluator/rubric, random seed where supported, runtime profile, budget, permissions snapshot and case IDs. Outputs include per-case status/result/score, reasons for missing evidence, raw artifact references, cost and performance metrics. Repeated execution may be nondeterministic; record replicates and uncertainty rather than promising identical generations.

External checkpoint integration carries provider identity, training run reference, artifact digest and suite subscription; deduplicate events before allocating evaluation budget. A checkpoint is not promoted solely because its training loss fell or a judge liked a small sample.

## Streaming distinctions

1. Output streaming: SSE chunks for a finite inference request; existing journal semantics apply.
2. Live input windows: timestamped overlapping video windows, bounded buffers, sampling, dropped-frame policy and event dedup; each inference consumes only data available at that time.
3. Stateful model sessions: model-specific persistent state and session lifecycle. Neither of the first two implies this capability.

Robotics adapters also version sensor timestamps, normalization, observation age, action dimensions/horizon, execution validity and emergency/fallback behavior supplied by the robot integration. Cloud, site-local and onboard placement must be measured for the intended task. OpenPI's remote policy interface is the starting adapter reference, not a universal real-time guarantee. [OpenPI documentation](https://github.com/Physical-Intelligence/openpi/blob/main/docs/remote_inference.md).
