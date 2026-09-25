/**
 * A3: the Docs and Models pages' words, as data. Every figure is read from the published record
 * (`PublishedModel`) or its price view; the fixed sentences are the decided public copy:
 * the charge disclosure is P-01's (15-pending-inputs.md, R21/R106), the revocation copy P-26's.
 * Nothing here says "zero data retention", a 120-second limit or that content is never stored
 * (RV-01): serving stores what `retention.content_stored` lists, whatever the trace setting.
 *
 * Imported by `node --test`: relative `.ts` imports only (R48).
 */
import { displayCredit, type Credit } from "../../../lib/contracts/v2/money-units.ts";
import type { Capability, PublishedModel, ServingRetention, StoredContent } from "../../../lib/contracts/v2/published-model.ts";
import type { PriceView } from "../models/catalog.ts";

/** Where a developer asks for help (the address the sign-in page already publishes). */
export const SUPPORT_EMAIL = "hello@callbill.ai";

/** P-26, verbatim. */
export const REVOCATION_COPY =
  "Revoking a key stops new requests immediately. Reads and cancels by that key stop within 60 seconds while our account service is reachable. During an account-service outage, a revoked key may continue to read or cancel its own existing jobs until the service recovers. It can never start new work.";

/** The catalog could not be read: no figures, no fixture, a retry. */
export const CATALOG_UNAVAILABLE =
  "The model catalog is unavailable right now, so no model, limit or price is shown. Reload the page to try again.";
export const CATALOG_EMPTY =
  "No model is published for your account right now. Nothing here is callable until one is.";

/** "400.00000000" -> "400", "1200.50000000" -> "1,200.5": grouped, no unit, no trailing zeros. */
export function plainAmount(value: Credit): string {
  return displayCredit(value, 0).replace(/ credits$/, "");
}

/** P-01's failed-execution and rate disclosure, with the card's own rates. CREDIT regime only. */
export function chargeDisclosure(price: Extract<PriceView, { unit: "CREDIT" }>): {
  intro: string[];
  neverCharged: string[];
  outro: string;
} {
  return {
    intro: [
      `Charges are in CREDIT, a product unit with no cash value. CREDIT has no USD exchange rate. A request is charged ${plainAmount(price.input)} CREDIT per million input tokens (video frames and text after preprocessing) and ${plainAmount(price.output)} CREDIT per million output tokens, rounded half-up to 8 decimal places. We place a hold for the maximum a request can cost and release the unused part.`,
      "A request that completes is charged for its tokens. If you cancel a request or disconnect, it is charged only for tokens the model reports as consumed. If no usage is reported, nothing is charged.",
    ],
    neverCharged: [
      "requests we reject or refuse;",
      "requests that never ran;",
      "requests that time out on our side;",
      "requests that fail for platform reasons.",
    ],
    outro: "Usage we cannot determine is released after 24 hours and never debited later.",
  };
}

/** Said beside a card the operator has not approved: it is shown, never presented as a price. */
export const PROVISIONAL_NOTE =
  "Provisional rate: this rate card has not been approved for launch and may change before it is.";

/** 86400 -> "24 hours", 604800 -> "7 days", 3600 -> "1 hour", 90 -> "90 seconds". */
export function duration(seconds: number): string {
  const unit = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (seconds % 86400 === 0 && seconds >= 172800) return unit(seconds / 86400, "day");
  if (seconds % 3600 === 0) return unit(seconds / 3600, "hour");
  if (seconds % 60 === 0) return unit(seconds / 60, "minute");
  return unit(seconds, "second");
}

/** 67108864 -> "64 MiB"; anything not a whole MiB in bytes. */
export function bytes(n: number): string {
  const mib = 1024 * 1024;
  return n % mib === 0 ? `${n / mib} MiB` : `${n.toLocaleString("en-US")} bytes`;
}

const STORED: Record<StoredContent, string> = {
  request_payload: "the request body you sent",
  source_media: "the source video",
  prepared_media: "the prepared (sampled) video frames",
  result: "the model's answer",
  stream_journal: "the streamed output events",
};

/** What serving keeps and for how long, from the record. Never "zero retention". */
export function retentionFacts(r: ServingRetention): string[] {
  const facts = [
    `To run a request, we store ${(Object.keys(STORED) as StoredContent[]).filter((c) => r.content_stored.includes(c)).map((c) => STORED[c]).join(", ")}. This is not zero data retention.`,
    `A result stays readable for ${duration(r.result_ttl_s)} after the request finishes; after that GET /v1/jobs/{handle}/result answers 410 result_expired. The job's status, cause and usage remain.`,
    `Streamed output can be replayed from Last-Event-ID for ${duration(r.stream_journal_ttl_s)}.`,
    `An Idempotency-Key keeps answering with its original job for ${duration(r.idempotency_ttl_s)} after the job finishes.`,
    `Prepared video may be kept in a processing cache for up to ${duration(r.processing_cache_ttl_s)}.`,
  ];
  if (r.physical_deletion_bound_s !== undefined) {
    facts.push(`Expired content is physically deleted within ${duration(r.physical_deletion_bound_s)}.`);
  }
  facts.push(
    "Trace capture is off by default. Turning it off does not delete the serving data above; it only stops optional trace collection.",
  );
  return facts;
}

/** The finite-video input contract, from the record. */
export function videoFacts(c: Capability): string[] {
  const v = c.video;
  if (!v) return ["This model does not accept video."];
  return [
    `One finished video per request, up to ${v.max_seconds} seconds and ${bytes(v.max_bytes)} decoded.`,
    `Accepted types: ${v.mime_types.join(", ")}.`,
    `Frames are sampled at ${v.fps} per second, ${v.min_frames} to ${v.max_frames} frames, at most ${v.max_pixels_per_frame.toLocaleString("en-US")} pixels per frame (profile ${v.profile_version}).`,
    "Send it as an https URL we can fetch, as an uploaded file (infrx-upload:<handle>), or inline as a base64 data: URL.",
    `The whole request body is at most ${bytes(c.max_request_bytes)}.`,
    v.live_stream
      ? "Live video streams are accepted."
      : "Live video streams are not supported: send a finished clip. Streaming applies to the text output only.",
  ];
}

/** The token and parameter contract, from the record. */
export function requestFacts(c: Capability): string[] {
  return [
    `Up to ${c.max_input_tokens.toLocaleString("en-US")} input tokens and ${c.max_output_tokens.toLocaleString("en-US")} output tokens (${c.max_context_tokens.toLocaleString("en-US")} in total).`,
    `Accepted fields: ${c.parameters.join(", ")}.`,
    `Refused with 400 unsupported_parameter: ${c.unsupported_parameters.join(", ")}. Any other field is refused the same way; there are no tools, function calling or structured output.`,
    `Execution modes: ${c.execution_modes.join(", ")}. Output is text.`,
  ];
}

/** [code, HTTP status, what to do] - each code and status is the gateway's (tests/a/content.test.ts). */
export const ERROR_ROWS: [string, number, string][] = [
  ["invalid_request", 400, "Fix the request; the message and param name the field."],
  ["unsupported_parameter", 400, "Remove the field; only the accepted fields above are served."],
  ["unsupported_media", 400, "Send one video of an accepted type, by https URL, upload handle or data: URL."],
  ["media_fetch_failed", 400, "We could not download the video URL; check it is public and reachable."],
  ["context_length_exceeded", 400, "Shorten the text or the clip, or lower max_tokens."],
  ["invalid_api_key", 401, "The key is missing, unknown or revoked; create a new one on API Keys."],
  ["insufficient_credit", 402, "Your available CREDIT cannot cover this request's hold. The grant is not refilled."],
  ["not_found", 404, "Unknown model, job or upload, or one that is not yours."],
  ["idempotency_conflict", 409, "This Idempotency-Key was used with a different body or mode; use a new key."],
  ["result_pending", 409, "The job is still running; poll its status."],
  ["result_expired", 410, "The result passed its retention window and cannot be read again."],
  ["request_too_large", 413, "The body or the video is over the limit; upload the file or shorten the clip."],
  ["capacity_exhausted", 429, "Busy: wait the Retry-After seconds, then retry with the same Idempotency-Key."],
  ["rate_limited", 429, "Too many requests: wait the Retry-After seconds, then retry."],
  ["internal_error", 500, "Our failure; retry with the same Idempotency-Key, and contact support with the Inference-Id if it persists."],
  ["dependency_unavailable", 503, "Temporarily unavailable: wait the Retry-After seconds, then retry with the same Idempotency-Key."],
  ["deadline_exceeded", 504, "The request ran out of time on our side; it is not charged. Retry."],
];

/** The routes a consumer calls (each is in the gateway's route table: tests/a/content.test.ts). */
export const ROUTE_ROWS: [string, string, string][] = [
  ["POST", "/v1/chat/completions", "Chat: JSON by default, SSE with \"stream\": true, a 202 job with Prefer: respond-async."],
  ["POST", "/v1/jobs", "An asynchronous job: the chat body, answered 202 once durably accepted."],
  ["GET", "/v1/jobs/{handle}", "The job's status, cause, usage and result_expires_at."],
  ["GET", "/v1/jobs/{handle}/result", "The result (409 while running, 410 once expired)."],
  ["GET", "/v1/jobs/{handle}/events", "Replay the job's output as SSE from Last-Event-ID."],
  ["DELETE", "/v1/jobs/{handle}", "Cancel the job."],
  ["POST", "/v1/uploads", "Create an upload: max_bytes, accepted_mime, optional bytes and digest."],
  ["PUT", "/v1/uploads/{handle}", "Send the video bytes with their Content-Type."],
  ["POST", "/v1/uploads/{handle}/complete", "Finalize (no body); then send infrx-upload:<handle>."],
  ["GET", "/v1/models", "This catalog, public, no key needed."],
];

/** Every sentence a page may render for `model`, for the copy guard. */
export function allCopy(model: PublishedModel, price: PriceView): string[] {
  const out = [
    REVOCATION_COPY,
    CATALOG_UNAVAILABLE,
    CATALOG_EMPTY,
    PROVISIONAL_NOTE,
    ...retentionFacts(model.retention),
    ...videoFacts(model.capability),
    ...requestFacts(model.capability),
    ...ERROR_ROWS.map((r) => r[2]),
    ...ROUTE_ROWS.map((r) => r[2]),
  ];
  if (price.unit === "CREDIT") {
    const d = chargeDisclosure(price);
    out.push(...d.intro, ...d.neverCharged, d.outro);
  }
  return out;
}
