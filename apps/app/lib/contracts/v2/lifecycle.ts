/**
 * F2C.a — the console half of `infrx/contracts/v2/lifecycle.py`.
 *
 * Browser-safe only: the vocabularies, the refusal -> code map, the upload ticket (it
 * carries no object key) and the readiness VIEW. The content rows, claims, tombstones and
 * manifests carry server object keys and stay in Python; no browser receives them.
 *
 * The decoders are exact: a missing required field, an unexpected field, a wrong schema
 * version or a broken invariant throws a `TypeError`, deterministically, so an old or
 * foreign payload can never be read as an "empty" or "available" one. Fixtures are read
 * from the Python package's `fixtures/v2/` (one source, no copy).
 *
 * Owned by the coordinator: a change here is a contract revision.
 */

import { instantKey } from "./types.ts";

export const UPLOAD_STATES = ["created", "finalized", "aborted", "expired"] as const;
export type UploadState = (typeof UPLOAD_STATES)[number];

/** `not_ready` = no committed marker (never completed). NOT the empty manifest. */
export const READINESS_STATES = ["not_ready", "ready"] as const;
export type ReadinessState = (typeof READINESS_STATES)[number];

export const CONTENT_KINDS = [
  "upload_destination",
  "source",
  "prepared",
  "payload",
  "result",
] as const;
export type ContentKind = (typeof CONTENT_KINDS)[number];

export const CONTENT_LOCATIONS = ["object_store", "database"] as const;
export const CONTENT_ORIGINS = ["written", "discovered"] as const;
export const LIFECYCLE_STATES = ["live", "tombstoned", "deleted"] as const;
export type LifecycleState = (typeof LIFECYCLE_STATES)[number];

export const LIFECYCLE_REFUSALS = [
  "not_found",
  "invalid_constraints",
  "upload_expired",
  "upload_not_open",
  "upload_not_finalized",
  "nothing_received",
  "bytes_changed",
  "too_large",
  "size_mismatch",
  "digest_mismatch",
  "mime_not_accepted",
  "invalid_manifest",
  "expectation_mismatch",
  "not_ready",
  "content_retiring",
  "not_eligible",
  "reference_live",
  "claim_held",
  "claim_lost",
] as const;
export type LifecycleRefusal = (typeof LIFECYCLE_REFUSALS)[number];

/** Reason -> the existing error code it is raised as. No new public code exists. */
export const LIFECYCLE_REFUSAL_CODES: Readonly<Record<LifecycleRefusal, string>> = Object.freeze({
  not_found: "not_found",
  invalid_constraints: "invalid_request",
  upload_expired: "upload_expired",
  upload_not_open: "state_conflict",
  upload_not_finalized: "invalid_request",
  nothing_received: "invalid_request",
  bytes_changed: "state_conflict",
  too_large: "request_too_large",
  size_mismatch: "invalid_request",
  digest_mismatch: "unsupported_media",
  mime_not_accepted: "unsupported_media",
  invalid_manifest: "invalid_request",
  expectation_mismatch: "invalid_request",
  not_ready: "not_claimable",
  content_retiring: "dependency_unavailable",
  not_eligible: "not_claimable",
  reference_live: "not_claimable",
  claim_held: "not_claimable",
  claim_lost: "stale_lease",
});

export const DESTINATION_SCHEME = "infrx-upload:";
const V2 = 2;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const UPLOAD_HANDLE = /^upl_[A-Za-z0-9_-]{22,64}$/;
const SHA256 = /^sha256:[0-9a-f]{64}$/;

export type UploadConstraints = {
  schema_version: 2;
  max_bytes: number;
  bytes?: number;
  accepted_mime: readonly string[];
  digest?: string;
};
export type UploadReceipt = { schema_version: 2; bytes: number; digest: string; received_at: string };
export type FinalizedSource = {
  schema_version: 2;
  content_id: string;
  generation: number;
  digest: string;
  bytes: number;
  mime: string;
  profile_version: string;
  duration_s: number;
  finalized_at: string;
};
export type UploadTicket = {
  schema_version: 2;
  upload_handle: string;
  org_id: string;
  destination_ref: string;
  constraints: UploadConstraints;
  state: UploadState;
  created_at: string;
  expires_at: string;
  received?: UploadReceipt;
  finalized?: FinalizedSource;
  refusal?: LifecycleRefusal;
};
export type ReadinessView = {
  schema_version: 2;
  request_id: string;
  state: ReadinessState;
  source_count?: number;
  ready_at?: string;
};

// --- exact decoding ------------------------------------------------------------------------
type Obj = Record<string, unknown>;

function fail(what: string): never {
  throw new TypeError(`contract violation: ${what}`);
}

/** The object with exactly `required` (all present) plus any of `optional`, and v2. */
function exact(value: unknown, what: string, required: string[], optional: string[] = []): Obj {
  if (typeof value !== "object" || value === null || Array.isArray(value)) fail(`${what} is not an object`);
  const obj = value as Obj;
  for (const key of ["schema_version", ...required]) {
    if (!(key in obj) || obj[key] === undefined || obj[key] === null) fail(`${what}.${key} is missing`);
  }
  const allowed = new Set(["schema_version", ...required, ...optional]);
  for (const key of Object.keys(obj)) if (!allowed.has(key)) fail(`${what}.${key} is unexpected`);
  if (obj.schema_version !== V2) fail(`${what}.schema_version is not 2`);
  for (const key of optional) if (key in obj && obj[key] === null) fail(`${what}.${key} is null`);
  return obj;
}

function text(obj: Obj, key: string, pattern?: RegExp): string {
  const value = obj[key];
  if (typeof value !== "string" || value.length === 0 || (pattern && !pattern.test(value))) {
    fail(`${key} is not a valid string`);
  }
  return value;
}

function count(obj: Obj, key: string, min: number): number {
  const value = obj[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < min) fail(`${key} is not an integer >= ${min}`);
  return value;
}

function instant(obj: Obj, key: string): string {
  const value = text(obj, key);
  instantKey(value); // throws on anything but a UTC contract instant
  return value;
}

function member<T extends string>(obj: Obj, key: string, allowed: readonly T[]): T {
  const value = obj[key];
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) fail(`${key} is not one of ${allowed.join("|")}`);
  return value as T;
}

function before(a: string, b: string): boolean {
  return instantKey(a) < instantKey(b);
}

function decodeConstraints(value: unknown): UploadConstraints {
  const obj = exact(value, "constraints", ["max_bytes", "accepted_mime"], ["bytes", "digest"]);
  const max = count(obj, "max_bytes", 1);
  if ("bytes" in obj && count(obj, "bytes", 1) > max) fail("declared bytes exceed max_bytes");
  if ("digest" in obj) text(obj, "digest", SHA256);
  const mimes = obj.accepted_mime;
  if (!Array.isArray(mimes) || mimes.length === 0 || new Set(mimes).size !== mimes.length
      || !mimes.every((m) => typeof m === "string" && m.length > 0 && m === m.trim().toLowerCase())) {
    fail("accepted_mime is a list of distinct lowercase media types");
  }
  return obj as UploadConstraints;
}

/** An upload ticket, with every rule `UploadTicket._one_fact` enforces in Python. */
export function decodeUploadTicket(value: unknown): UploadTicket {
  const obj = exact(value, "ticket",
    ["upload_handle", "org_id", "destination_ref", "constraints", "state", "created_at", "expires_at"],
    ["received", "finalized", "refusal"]);
  const handle = text(obj, "upload_handle", UPLOAD_HANDLE);
  text(obj, "org_id", UUID);
  if (obj.destination_ref !== DESTINATION_SCHEME + handle) fail("destination_ref is infrx-upload:<handle>");
  const c = decodeConstraints(obj.constraints);
  const state = member(obj, "state", UPLOAD_STATES);
  const created = instant(obj, "created_at");
  const expires = instant(obj, "expires_at");
  if (!before(created, expires)) fail("expires_at must follow created_at");
  if ((state === "aborted") !== ("refusal" in obj)) fail("aborted exactly when a refusal is recorded");
  if ("refusal" in obj) member(obj, "refusal", LIFECYCLE_REFUSALS);
  let received: Obj | undefined;
  if ("received" in obj) {
    received = exact(obj.received, "received", ["bytes", "digest", "received_at"]);
    if (count(received, "bytes", 0) > c.max_bytes) fail("received over max_bytes");
    text(received, "digest", SHA256);
    const at = instant(received, "received_at");
    if (before(at, created) || !before(at, expires)) fail("received outside the window");
  }
  if ((state === "finalized") !== ("finalized" in obj)) fail("finalized exactly when a source is recorded");
  if ("finalized" in obj) {
    const done = exact(obj.finalized, "finalized", ["content_id", "generation", "digest", "bytes", "mime",
      "profile_version", "duration_s", "finalized_at"]);
    text(done, "content_id", UUID);
    count(done, "generation", 1);
    const digest = text(done, "digest", SHA256);
    const bytes = count(done, "bytes", 0);
    const mime = text(done, "mime");
    text(done, "profile_version");
    if (typeof done.duration_s !== "number" || !(done.duration_s >= 0)) fail("duration_s is a measured number");
    const at = instant(done, "finalized_at");
    if (!received || received.digest !== digest || received.bytes !== bytes) fail("a finalized source is exactly the bytes received");
    if (!c.accepted_mime.includes(mime) || (c.bytes !== undefined && c.bytes !== bytes)
        || (c.digest !== undefined && c.digest !== digest)) fail("a finalized source satisfies every constraint");
    if (before(at, received.received_at as string) || !before(at, expires)) fail("finalized outside the window");
  }
  return obj as UploadTicket;
}

/** `ready` carries a count (possibly 0) and an instant; `not_ready` neither. */
export function decodeReadinessView(value: unknown): ReadinessView {
  const obj = exact(value, "readiness", ["request_id", "state"], ["source_count", "ready_at"]);
  text(obj, "request_id", UUID);
  const ready = member(obj, "state", READINESS_STATES) === "ready";
  if (ready !== ("source_count" in obj) || ready !== ("ready_at" in obj)) {
    fail("ready carries source_count and ready_at; not_ready neither");
  }
  if (ready) {
    count(obj, "source_count", 0);
    instant(obj, "ready_at");
  }
  return obj as ReadinessView;
}

/** A finalized upload is usable while `now < expires_at`; at equality it has expired. */
export function uploadUsable(ticket: UploadTicket, now: string): boolean {
  return ticket.state === "finalized" && before(now, ticket.expires_at);
}
