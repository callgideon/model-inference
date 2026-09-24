/**
 * The published-model projection — F2C.c, the console twin of
 * `apps/infrx-api/infrx/contracts/v2/published_model.py`.
 *
 * One record says what a caller may send to a model, how it is priced and what serving keeps.
 * The catalog and docs (A3) read it instead of `public.models`' USD columns and the stale copy
 * (120 s, "never stored"), so a page cannot claim what the gateway refuses (RV-01).
 *
 * Both halves read the same fixtures (`infrx/contracts/v2/published/`), refuse the same
 * malformed records and re-serialize a record to the same canonical bytes (`canonicalJson`,
 * the twin of `codec.canonical_bytes`). `tests/contracts/v2/test_published_model.py` holds the
 * rule tables and the name grammar below equal to the Python ones.
 *
 * Rules (proposed ruling R109): resolution is `splitModel` + `resolveModel`, once; pricing
 * follows resolution, never the spelling, and names exactly one price identity — the CREDIT
 * card in the credit regime, the USD price keyed by the canonical revision in the legacy one.
 * There is no conversion. A projection never advertises what serving refuses
 * (`profileViolations`). Serving keeps content: no record here can claim zero data retention.
 *
 * Owned by the coordinator: a change here is a contract revision.
 */

import { parseCredit, parseUsd, type Credit, type Usd } from "./money-units.ts";
import { instantKey } from "./types.ts";

// 0008 `resolve_admission_pins`' pattern, with the two parts captured.
export const MODEL_GRAMMAR = /^([^@\s]{1,200})(?:@([^@\s]{1,64}))?$/u;

/** What serving stores for an accepted request whatever the trace mode. */
export const STORED_CONTENT = [
  "prepared_media",
  "request_payload",
  "result",
  "source_media",
  "stream_journal",
] as const;
export type StoredContent = (typeof STORED_CONTENT)[number];

export const EXECUTION_MODES = ["sync", "stream", "async"] as const;
export type ExecutionMode = (typeof EXECUTION_MODES)[number];

// The profile rules: caps may be advertised at or below the enforced value, vocabularies only
// as subsets, the preprocessing constraints and retention only exactly.
export const CAPS = [
  "max_request_bytes",
  "max_context_tokens",
  "max_input_tokens",
  "max_output_tokens",
] as const;
export const SUBSETS = [
  "input_modalities",
  "output_modalities",
  "execution_modes",
  "parameters",
] as const;
export const VIDEO_CAPS = ["max_seconds", "max_bytes", "max_per_request"] as const;
export const VIDEO_EXACT = [
  "profile_version",
  "fps",
  "min_frames",
  "max_frames",
  "max_pixels_per_frame",
] as const;

type V2 = { schema_version?: 2 };

export type VideoInput = V2 & {
  max_seconds: number;
  max_bytes: number;
  max_per_request: number;
  mime_types: string[];
  live_stream: boolean;
  profile_version: string;
  fps: number;
  min_frames: number;
  max_frames: number;
  max_pixels_per_frame: number;
};

export type Capability = V2 & {
  input_modalities: ("text" | "video")[];
  output_modalities: "text"[];
  execution_modes: ExecutionMode[];
  parameters: string[];
  unsupported_parameters: string[];
  max_request_bytes: number;
  max_context_tokens: number;
  max_input_tokens: number;
  max_output_tokens: number;
  video?: VideoInput;
};

export type ServingRetention = V2 & {
  zero_data_retention: false;
  capture_off_deletes_serving_content: false;
  trace_capture_default: "off";
  content_stored: StoredContent[];
  result_ttl_s: number;
  stream_journal_ttl_s: number;
  idempotency_ttl_s: number;
  processing_cache_ttl_s: number;
  physical_deletion_bound_s?: number;
};

export type ServingIdentity = V2 & {
  serving_version_id: string;
  revision_label: string;
  model_repo: string;
  model_commit: string;
  weight_shard_digests: string[];
  adapter_digest?: string;
  tokenizer_digest: string;
  chat_template_digest: string;
  digest_source: "served_bytes" | "registry_oid" | "registry_oid_confirmed";
  prompt_harness_ref: string;
  preprocessor_profile_version: string;
  runtime_image_ref: string;
  runtime_image_digest?: string;
  engine_options_digest: string;
  precision: string;
};

export type CreditRate = V2 & {
  rate_card_version: string;
  unit: "CREDIT";
  meter: "tokens-v1";
  input_rate_per_million: Credit;
  output_rate_per_million: Credit;
  effective_at: string;
  provisional: boolean;
};

export type UsdPrice = V2 & {
  price_version: string;
  unit: "USD";
  model_revision: string;
  input_rate_per_million: Usd;
  output_rate_per_million: Usd;
  token_rules_version: string;
};

export type Pricing = V2 & {
  regime: "legacy_usd" | "credit";
  credit?: CreditRate;
  legacy_usd?: UsdPrice;
};

export type PublishedModel = V2 & {
  object: "model";
  id: string;
  created: number;
  owned_by: string;
  model_revision: string;
  aliases: string[];
  listing_version: number;
  deployment_revision_id: string;
  serving: ServingIdentity;
  capability: Capability;
  pricing: Pricing;
  retention: ServingRetention;
  availability: "available" | "unavailable";
  availability_as_of: string;
};

export type ServingProfile = V2 & { capability: Capability; retention: ServingRetention };

export type PricedResolution = V2 & {
  requested_model: string;
  model_revision: string;
  deployment_revision_id: string;
  serving_version_id: string;
  accounting_regime: "legacy_usd" | "credit";
  rate_card_version?: string;
  price_version?: string;
};

/** A typed refusal with the contract's error code (`not_found`, `invalid_request`). */
export class ContractRefusal extends Error {
  readonly code: "not_found" | "invalid_request";

  constructor(code: "not_found" | "invalid_request", message: string) {
    super(message);
    this.code = code;
  }
}

// --- strict parsing: a closed record, every field typed, nothing coerced -------------------
type Check = (value: unknown, path: string) => void;

function fail(path: string, why: string): never {
  throw new TypeError(`${path}: ${why}`);
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const name: Check = (v, p) => {
  if (typeof v !== "string" || v.length === 0) fail(p, "a non-empty string");
};
const count: Check = (v, p) => {
  if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 1) fail(p, "an integer >= 1");
};
const bool: Check = (v, p) => {
  if (typeof v !== "boolean") fail(p, "a boolean");
};
const pattern =
  (re: RegExp, what: string): Check =>
  (v, p) => {
    if (typeof v !== "string" || !re.test(v)) fail(p, what);
  };
const uuid = pattern(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/, "a UUID");
const sha256 = pattern(/^sha256:[0-9a-f]{64}$/, "a sha256 digest");
const instant: Check = (v, p) => {
  try {
    instantKey(v as string);
  } catch {
    fail(p, "a UTC instant");
  }
};
const oneOf =
  (...values: readonly unknown[]): Check =>
  (v, p) => {
    if (!values.includes(v)) fail(p, `one of ${JSON.stringify(values)}`);
  };
const amount =
  (parse: (v: unknown) => string): Check =>
  (v, p) => {
    let canonical: string | null = null;
    try {
      canonical = parse(v);
    } catch {
      fail(p, "an exact decimal string");
    }
    if (canonical !== v || (v as string).startsWith("-")) fail(p, "a canonical nonnegative amount");
  };
const list =
  (item: Check, min = 0): Check =>
  (v, p) => {
    if (!Array.isArray(v) || v.length < min) fail(p, `a list of at least ${min}`);
    v.forEach((x, i) => item(x, `${p}[${i}]`));
  };

function record(fields: Record<string, Check>, optional: readonly string[] = []): Check {
  return (v, p) => {
    if (!isObject(v)) fail(p, "an object");
    for (const key of Object.keys(v)) {
      if (key !== "schema_version" && !(key in fields)) fail(`${p}.${key}`, "not a field");
    }
    if ("schema_version" in v && v.schema_version !== 2) fail(`${p}.schema_version`, "2");
    for (const [key, check] of Object.entries(fields)) {
      const value = v[key];
      if (value === undefined || value === null) {
        if (!optional.includes(key)) fail(`${p}.${key}`, "required");
        continue;
      }
      check(value, `${p}.${key}`);
    }
  };
}

function sortedUnique(values: readonly string[], path: string): void {
  const sorted = [...new Set(values)].sort();
  if (sorted.length !== values.length || sorted.some((x, i) => x !== values[i])) {
    fail(path, "sorted, without duplicates");
  }
}

const videoInput = record({
  max_seconds: count,
  max_bytes: count,
  max_per_request: count,
  mime_types: list(name, 1),
  live_stream: bool,
  profile_version: name,
  fps: count,
  min_frames: count,
  max_frames: count,
  max_pixels_per_frame: count,
});

const capability = record(
  {
    input_modalities: list(oneOf("text", "video"), 1),
    output_modalities: list(oneOf("text"), 1),
    execution_modes: list(oneOf(...EXECUTION_MODES), 1),
    parameters: list(name, 1),
    unsupported_parameters: list(name),
    max_request_bytes: count,
    max_context_tokens: count,
    max_input_tokens: count,
    max_output_tokens: count,
    video: videoInput,
  },
  ["video"],
);

const retention = record(
  {
    zero_data_retention: oneOf(false),
    capture_off_deletes_serving_content: oneOf(false),
    trace_capture_default: oneOf("off"),
    content_stored: list(oneOf(...STORED_CONTENT), 1),
    result_ttl_s: count,
    stream_journal_ttl_s: count,
    idempotency_ttl_s: count,
    processing_cache_ttl_s: count,
    physical_deletion_bound_s: count,
  },
  ["physical_deletion_bound_s"],
);

const servingIdentity = record(
  {
    serving_version_id: uuid,
    revision_label: name,
    model_repo: name,
    model_commit: pattern(/^[0-9a-f]{40}$/, "a 40-hex commit"),
    weight_shard_digests: list(sha256, 1),
    adapter_digest: sha256,
    tokenizer_digest: sha256,
    chat_template_digest: sha256,
    digest_source: oneOf("served_bytes", "registry_oid", "registry_oid_confirmed"),
    prompt_harness_ref: name,
    preprocessor_profile_version: name,
    runtime_image_ref: name,
    runtime_image_digest: sha256,
    engine_options_digest: sha256,
    precision: name,
  },
  ["adapter_digest", "runtime_image_digest"],
);

const creditRate = record({
  rate_card_version: name,
  unit: oneOf("CREDIT"),
  meter: oneOf("tokens-v1"),
  input_rate_per_million: amount(parseCredit),
  output_rate_per_million: amount(parseCredit),
  effective_at: instant,
  provisional: bool,
});

const usdPrice = record({
  price_version: name,
  unit: oneOf("USD"),
  model_revision: name,
  input_rate_per_million: amount(parseUsd),
  output_rate_per_million: amount(parseUsd),
  token_rules_version: name,
});

const pricing = record(
  { regime: oneOf("legacy_usd", "credit"), credit: creditRate, legacy_usd: usdPrice },
  ["credit", "legacy_usd"],
);

const publishedModel = record({
  object: oneOf("model"),
  id: name,
  created: (v, p) => {
    if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 0) fail(p, "unix seconds");
  },
  owned_by: name,
  model_revision: name,
  aliases: list(name, 1),
  listing_version: count,
  deployment_revision_id: uuid,
  serving: servingIdentity,
  capability,
  pricing,
  retention,
  availability: oneOf("available", "unavailable"),
  availability_as_of: instant,
});

/** The cross-field rules of the Python validators, after the shape is known to be right. */
function checkCapability(c: Capability, p: string): void {
  for (const key of [
    "input_modalities",
    "output_modalities",
    "execution_modes",
    "parameters",
    "unsupported_parameters",
  ] as const) {
    sortedUnique(c[key], `${p}.${key}`);
  }
  if (c.parameters.some((x) => c.unsupported_parameters.includes(x))) {
    fail(p, "a parameter is either accepted or refused, not both");
  }
  if ((c.video === undefined || c.video === null) !== !c.input_modalities.includes("video")) {
    fail(`${p}.video`, "stated exactly when video input is accepted");
  }
  if (c.max_input_tokens + c.max_output_tokens > c.max_context_tokens) {
    fail(p, "max_input_tokens + max_output_tokens exceeds max_context_tokens");
  }
  if (c.video) {
    sortedUnique(c.video.mime_types, `${p}.video.mime_types`);
    if (c.video.min_frames > c.video.max_frames) fail(`${p}.video`, "min_frames > max_frames");
  }
}

function checkPricing(pr: Pricing, p: string): void {
  if (pr.regime === "credit" && !pr.credit) fail(p, "the credit regime needs an approved card");
  if (pr.regime === "legacy_usd" && !pr.legacy_usd) fail(p, "the legacy regime needs a USD price");
  if (pr.regime === "legacy_usd" && pr.credit) fail(p, "no CREDIT rate beside a USD regime");
}

export function parseServingProfile(value: unknown): ServingProfile {
  record({ capability, retention })(value, "profile");
  const profile = value as ServingProfile;
  checkCapability(profile.capability, "profile.capability");
  sortedUnique(profile.retention.content_stored, "profile.retention.content_stored");
  return profile;
}

/** The record, or a `TypeError` naming the first path that is wrong. Never coerces. */
export function parsePublishedModel(value: unknown): PublishedModel {
  publishedModel(value, "model");
  const m = value as PublishedModel;
  checkCapability(m.capability, "model.capability");
  sortedUnique(m.retention.content_stored, "model.retention.content_stored");
  checkPricing(m.pricing, "model.pricing");
  if (m.model_revision !== `${m.id}@${m.serving.revision_label}`) {
    fail("model.model_revision", "<id>@<serving revision label>");
  }
  sortedUnique(m.aliases, "model.aliases");
  if (!m.aliases.includes(m.model_revision)) fail("model.aliases", "must include the revision");
  if (m.aliases.some((a) => a !== m.id && a !== m.model_revision)) {
    fail("model.aliases", "an alias is the id or the pinned revision");
  }
  if (m.pricing.legacy_usd && m.pricing.legacy_usd.model_revision !== m.model_revision) {
    fail("model.pricing.legacy_usd.model_revision", "keyed by the canonical revision (P-22)");
  }
  return m;
}

// --- canonical bytes -------------------------------------------------------------------------
function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (!isObject(value)) return value;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort()) {
    if (value[key] !== null && value[key] !== undefined) out[key] = sortKeys(value[key]);
  }
  return out;
}

/** `codec.canonical_bytes`: sorted keys, two-space indent, trailing newline, no nulls. */
export function canonicalJson(value: unknown): string {
  return `${JSON.stringify(sortKeys(value), null, 2)}\n`;
}

// --- resolution and pricing, once ------------------------------------------------------------
export function splitModel(requested: unknown): [string, string | null] {
  const match = typeof requested === "string" ? MODEL_GRAMMAR.exec(requested) : null;
  if (match === null) throw new ContractRefusal("not_found", "model");
  return [match[1], match[2] ?? null];
}

export function resolveModel(requested: string, published: readonly PublishedModel[]): PublishedModel {
  const [id, label] = splitModel(requested);
  const matches = published.filter(
    (m) => m.id === id && (label === null || m.serving.revision_label === label),
  );
  if (matches.length === 0) throw new ContractRefusal("not_found", "model");
  return matches.reduce((best, m) => (m.listing_version > best.listing_version ? m : best));
}

/** The pins an admission records (P-22): the requested spelling beside the canonical revision. */
export function priceRequest(requested: string, published: readonly PublishedModel[]): PricedResolution {
  const model = resolveModel(requested, published);
  const credit = model.pricing.regime === "credit";
  return {
    schema_version: 2,
    requested_model: requested,
    model_revision: model.model_revision,
    deployment_revision_id: model.deployment_revision_id,
    serving_version_id: model.serving.serving_version_id,
    accounting_regime: model.pricing.regime,
    ...(credit
      ? { rate_card_version: model.pricing.credit!.rate_card_version }
      : { price_version: model.pricing.legacy_usd!.price_version }),
  };
}

// --- the profile-vs-projection check ------------------------------------------------------------
/** Each way `published` advertises what `profile` refuses, as `path: detail` (sorted by path). */
export function profileViolations(published: PublishedModel, profile: ServingProfile): string[] {
  const out: [string, string][] = [];
  const more = (path: string, advertised: unknown, enforced: unknown) =>
    out.push([path, `advertised ${JSON.stringify(advertised)}, enforced ${JSON.stringify(enforced)}`]);
  const ad = published.capability;
  const real = profile.capability;
  for (const key of CAPS) if (ad[key] > real[key]) more(`capability.${key}`, ad[key], real[key]);
  for (const key of SUBSETS) {
    const extra = (ad[key] as string[]).filter((x) => !(real[key] as string[]).includes(x)).sort();
    if (extra.length) more(`capability.${key}`, extra, real[key]);
  }
  const accepted = ad.unsupported_parameters.filter((x) => real.parameters.includes(x)).sort();
  if (accepted.length) more("capability.unsupported_parameters", accepted, "accepted");
  if (ad.video) {
    if (!real.video) {
      more("capability.video", "video", null);
    } else {
      for (const key of VIDEO_CAPS) {
        if (ad.video[key] > real.video[key]) more(`capability.video.${key}`, ad.video[key], real.video[key]);
      }
      for (const key of VIDEO_EXACT) {
        if (ad.video[key] !== real.video[key]) more(`capability.video.${key}`, ad.video[key], real.video[key]);
      }
      const extra = ad.video.mime_types.filter((x) => !real.video!.mime_types.includes(x)).sort();
      if (extra.length) more("capability.video.mime_types", extra, real.video.mime_types);
      if (ad.video.live_stream && !real.video.live_stream) more("capability.video.live_stream", true, false);
    }
  }
  const a = published.retention as Record<string, unknown>;
  const b = profile.retention as Record<string, unknown>;
  for (const key of [...new Set([...Object.keys(a), ...Object.keys(b)])]) {
    if (JSON.stringify(a[key] ?? null) !== JSON.stringify(b[key] ?? null)) more(`retention.${key}`, a[key], b[key]);
  }
  return out.sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0)).map(([path, detail]) => `${path}: ${detail}`);
}
