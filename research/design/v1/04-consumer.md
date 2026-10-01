# Consumer App experience specification

Preserve the existing App and its security/accounting contracts. The primary task is **get an endpoint working in the user's code**, then understand requests and charges. [Visual reference](design-reference.html) shows the proposed hierarchy; [audit](01-audit.md) explains the changes.

## C-01 — Account access

Keep the existing sign-in design, explicit labels, recovery and account verification flow. Public signup cannot be implied to work while its operational gate is closed. A trusted server flag drives “Sign up” vs “Access is currently by invitation”; never infer availability from the existence of `/sign-up`.

Registration copy: “A verified individual receives 10,000 promotional credits once. No monthly refill.” Grant confirmation appears only after the persisted grant exists. Email verification pending and credit grant pending are different states. Email delivery, resend cooldown, CAPTCHA and expired recovery token states need inline guidance and accessible errors. No extra design-required fields or organization setup.

Recovery: neutral delivery response, clear next step, preserve safe return target, and resend rate limits. Implement missing CAPTCHA/SMTP work through existing launch owners. Do not bypass auth safeguards to make a walkthrough pass.

## C-02 — Models and first call

Keep `/models` as the landing route. New-account top section: “Make your first Marlin request” with three steps: **Create a key → Make a test call → View your request**. This is compact guidance, not a mandatory onboarding overlay.

1. Create a key invokes the existing secure key dialog or links to `/api-keys` with a safe return route. Never put the secret in URL state. If active keys exist, step says “Use an existing key or create a new one”; do not require another credential.
2. Test call defaults to the smallest supported **text connectivity request**, labeled as a connectivity check, not a video/SOP quality test. Examples use `INFRX_API_KEY`; instructions explain assigning the full secret locally. No prefix, ellipsis or copied key label is inserted as a credential. A placeholder is visibly marked and cannot be confused with a working secret.
3. View your request links to Usage. Completion derives from the user's persisted successful request, never from clicking Copy. If the existing read only returns a bounded page, either use a scoped existence read or label the panel “Quickstart” without claiming lifetime completion. No local-storage “success” badge as evidence.

Completion removes the dominant guide but leaves a compact “Quickstart” link. No product telemetry required to infer this state. Optional collapse preference is local presentation only, not account readiness.

### Model presentation

One compact card per real published model:

- Friendly display: **Marlin 2B**, canonical ID copy action secondary.
- One sentence: “Describe recorded video clips and locate events in time.” No measured SOP accuracy or robot-action claim.
- Capability summary: finished video + text → text; output streaming and async supported only when catalog says so.
- Current availability with provenance/meaning. A listed catalog record is not proof of fleet health; do not label it “All systems operational.”
- CREDIT rate summary with input/output basis; link to charging explanation. No USD conversion, promised credits/clip or cost savings without measured workload.
- Primary action: “Set up a call”; secondary “API reference.” Advanced details disclose immutable revision, schema/runtime/rate card, supported fields and limits.

Show the most relevant video constraints next to the video guide, sourced from the catalog. At this audit they are one finished clip ≤82 seconds and ≤64 MiB, plus token/frame/body constraints; these numbers are **not hard-coded mockup facts** in production.

Unavailable catalog = clear retry state, not an empty marketplace. Unavailable model = “Not accepting requests” with detail if trusted; code can remain viewable as a labeled reference, but do not invite a live test that appears available. A change in published limits updates both model summary and generated examples.

### First video request

After text connectivity, offer “Use a video” with two paths: accessible HTTPS URL or upload a local clip through the documented client/API. No automatic fetch from a pasted URL in the console. A field for generating an example may validate the string locally but is not an upload or network action.

Clearly label replacement values; `example.com/clip.mp4` must not masquerade as a runnable demo. A hosted sample clip is optional only if a rights-cleared, immutable, reachable asset is added, measured under current bounds, versioned and monitored. Until then, require the user's clip and give the existing upload procedure.

For large datasets, link the bounded/resumable batch client guide and explain per-item idempotency, status, result expiry and output collection. The console does not yet ingest a robotics dataset for consumer batch inference. Avoid implying that provider dataset imports are the consumer batch API.

## C-03 — API keys

Route `/api-keys`. Empty state: “Create a key to call Marlin from your code” with Create key button in the card. Keep header action once populated. Rows: name, public prefix, created, last used if actually provided, active/revoked, action. No full secret ever returned from a listing.

Create dialog: label Name, example “Robotics evaluation”; short scope explanation. On success, show one-time secret, copy button and “Save this key now. You won't be able to view it again.” Secret remains in memory only. Clipboard failure shows selectable text and accurate failure, not “Copied.” Close acknowledges loss when needed; never block users indefinitely if clipboard is unavailable. Link “Continue to quickstart” without sending the secret through URL or server.

Preserve the current stable attempt UUID. A lost response uses the same operation identity; “Created, secret no longer available” is a distinct outcome, not permission to mint repeated keys. Revoke confirmation identifies key by name/prefix and describes new-request vs existing-read behavior using the actual contract. Pending revoke, failure and persisted revoked state are separate. Revocation of an active key must not silently create a replacement.

## C-04 — Usage and request detail

Keep `/usage`. Change page label to “Usage” with explanatory subtitle “Requests, results and credit charges.” Request activity comes first; credit balance is a compact link/summary, with detailed accounting on Credits. Supported filters only: time, key, model as existing APIs allow. A catalog-backed model picker may replace a freeform ID only if all relevant historical IDs remain selectable.

Empty account: “Your requests will appear here” + Set up a call. Empty filtered result: “No requests match these filters” + Clear filters. Unavailable query: “We couldn’t load usage” + Retry; never display zero spent from a failed query. Cursor pagination, filter state and back navigation must remain stable.

List rows prioritize request identifier, model, start time, execution state and charge **only when authoritative**, followed by mode and existing measured duration. Execution state and financial state are distinct: a running request can have a reserved amount, a finished request can await accounting. Do not call a hold “spent.” Use existing typed status mapping; unknown values render “Unknown status” rather than success.

Detail hierarchy:

1. Header: request ID + status, model/revision, time and safe Copy request ID.
2. Result panel: readable answer or explicit pending/expired/unavailable state; expiry timestamp and remaining access context; Copy result / Download when readable.
3. Usage and charge: input/output tokens, rate card, reserved/released/charged states from their own fields; exact precision in details, readable rounded summary without claiming rounded zero is exact zero.
4. Technical details: serving identity, cause/error and diagnostic IDs, supported retry guidance.

Result content is untrusted text. Do not render arbitrary HTML. Preserve no-store fetches, in-memory-only content, expiry timers, bfcache revalidation, unmount cleanup and clearing on lost access. At expiry: “This result is no longer available. Status and usage remain available.” Do not claim physical deletion happened. If a read fails, “Retry loading result” only re-reads; no silent new inference or new charge.

Pending status uses existing bounded polling; show last checked and manual refresh after its limit. User navigation must not cancel durable inference. If exposing Cancel later, use an existing verified authorization/action contract and handle races; it is not required for the visual redesign.

## C-05 — Credits

Keep `/billing`, display title Credits. Primary number: **Available to use**. Secondary: Reserved for requests, Spent, Balance (available + reserved), with the exact existing ledger meanings verified before copy changes. Reserve and spend are never merged. Use eight-decimal exact values internally; avoid float conversion, and make precise charge amounts available in details.

Grant panel: “10,000 promotional credits, granted once after verification.” Show actual receipt/date when available, grant pending when appropriate; no reset date, monthly meter, Top up, subscription upsell or dollar value. If available credits cannot cover a hold, explain why the request is refused and link current policy/support without implying paid purchase is supported.

Ledger: friendly event labels from a fixed mapping (e.g. “Promotional credit grant”); raw reason and operation/reference IDs in details. Keep signs, units, running balance semantics and pagination intact. Unknown reason has a neutral fallback with raw code available, not a misleading known event. Wallet unavailable stays unavailable.

## C-06 — Documentation

Keep contract-backed content and existing anchor URLs. Add desktop section navigation and mobile Contents disclosure. Recommended order: Quickstart → Video input → Async and large datasets → Streamed output → Results/retries → Limits → Credits → Data retention → Keys → Errors/support. The screenshot reference illustrates first-use hierarchy, not a replacement API spec.

Code examples retain language tabs and generated contract inputs. Requirements:

- `INFRX_API_KEY` remains an environment variable. Display key labels/prefixes only as metadata.
- Input replacements are explicit and safely escaped; do not interpolate arbitrary URL/prompt text into shell code unsafely.
- Async examples have bounded polling, current `Retry-After` behavior, terminal failure handling and no fetch-result after failed/cancelled completion. Follow the supported client's semantics; test the displayed commands against controlled fixtures.
- Idempotency guarantee states the key retention window; never promise perpetual exactly-once inference across expired identities.
- Streaming means **text output**; video input is a completed clip.
- No structured output, tool use, live-video or universal OpenAI API support implied. “OpenAI-compatible Chat Completions for supported fields” is the accurate scope.
- Physical deletion `null`/absent means unknown, not zero. A numeric zero must not become a public immediate-deletion guarantee without approved evidence and clarified contract semantics. Unknown facts cannot be fixed by hiding all retention information.

Status and error guidance should say what to do next, include the request/Inference ID and never suggest sending API secrets to support. Show generated example version/source as a secondary detail; CI contract coverage is not live production acceptance.

## C-07 — Settings and privacy

Retain Account and Privacy/data groups. Serving retention, optional trace capture, sharing/evaluation/training and account export/deletion are separate concepts. Read-only policies stay read-only; no cosmetic toggle that cannot persist safely. Changing privacy claims requires evidence from the backend policy, not a design decision.

Link serving-retention details to Docs. “Request result access expires after…” must remain separate from physical cleanup. Data export/account deletion remain documented support processes if not automated; do not show a destructive fake button. Preserve the actual support address already published by the App.

## Success criteria

The implementation must let a new invited user find key creation, distinguish a connectivity test from video analysis, create exactly one key, run a valid request in code, find its result and reconcile the charge. The user must also understand that the credit grant is one-time and that a finished clip is required. These are tested with actual persisted outcomes, not completed checkboxes or mock animations.
