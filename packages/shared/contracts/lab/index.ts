/**
 * Lab contracts (F3), the TypeScript half of `apps/infrx-api/infrx/contracts/lab/`.
 *
 * Same refs, same eleven `lab.*.1` schemas, same five refusal reasons in the same
 * order, same state machines and idempotency keys. `fixtures.json` beside this file is
 * read by both halves; `apps/infrx-api/tests/contracts/lab` runs this module under Node
 * and fails if any verdict, reason, ref or vocabulary differs from Python's.
 *
 * The server (Python) is the authority: this module lets the Lab UI refuse early and
 * render the same reasons. It never authorizes anything - rights are checked at the
 * server's gates against the current grant (DATA-RIGHTS).
 *
 * Dependency-free and browser-safe (no node: imports); the fake lives in `fakes.ts`.
 */

export const SURFACE_VERSION = "contracts-lab.1";

export const SCHEMAS = [
  "lab.dataset_manifest.1", "lab.harness_revision.1", "lab.eval_run.1", "lab.attempt.1",
  "lab.checkpoint.1", "lab.annotation.1", "lab.external_run.1", "lab.rollout_policy.1",
  "lab.rollout_assignment.1", "lab.rollout_decision.1", "lab.optimization_variant.1",
] as const;
export type Schema = (typeof SCHEMAS)[number];

export const REF_KINDS = [
  "source", "grant", "dataset", "harness", "serving", "evaluator", "run", "external_run",
  "checkpoint", "annotation", "rubric", "payer", "policy", "variant",
] as const;
export type RefKind = (typeof REF_KINDS)[number];

export const MODALITIES = ["text", "finite_video", "structured"] as const;
export const MAX_VIDEO_MS = 82_000;
export const REASONS = [
  "unknown_schema", "mutable_ref", "cross_provider_ref", "mixed_units", "invalid",
] as const;
export type Reason = (typeof REASONS)[number];
export const LAB_UNITS = ["CREDIT", "PROVIDER_USD"] as const;

/** A state that is not a key is terminal. `ambiguous` never returns to `submitting`. */
export const TRANSITIONS: Readonly<Record<string, Readonly<Record<string, readonly string[]>>>> = {
  run: { queued: ["cancelled", "running"], running: ["cancelled", "failed", "succeeded"] },
  case: { pending: ["leased", "skipped"], leased: ["done", "failed", "pending"] },
  attempt: { leased: ["expired", "failed", "succeeded"] },
  harness: { draft: ["frozen"], frozen: ["retired"] },
  checkpoint: { received: ["rejected", "validated"], validated: ["evaluated", "rejected"] },
  annotation: { draft: ["submitted"], submitted: ["accepted", "rejected"], accepted: ["superseded"] },
  external_run: {
    prepared: ["cancelled", "submitting"],
    submitting: ["ambiguous", "failed", "submitted"],
    ambiguous: ["failed", "submitted"],
    submitted: ["cancelled", "completed", "failed"],
  },
};

function statesOf(kind: string): string[] {
  const table = TRANSITIONS[kind] ?? {};
  return [...new Set([...Object.keys(table), ...Object.values(table).flat()])];
}

/** schema -> [ref kind, id field] for the records other records point at. */
export const REFERABLE: Readonly<Partial<Record<Schema, readonly [RefKind, string]>>> = {
  "lab.dataset_manifest.1": ["dataset", "dataset_id"],
  "lab.harness_revision.1": ["harness", "harness_id"],
  "lab.eval_run.1": ["run", "run_id"],
  "lab.checkpoint.1": ["checkpoint", "checkpoint_id"],
  "lab.annotation.1": ["annotation", "annotation_id"],
  "lab.external_run.1": ["external_run", "external_run_id"],
  "lab.rollout_policy.1": ["policy", "policy_id"],
  "lab.optimization_variant.1": ["variant", "variant_id"],
};

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
/** `lab:<kind>:<provider_org_id>:<object_id>@sha256:<64 hex>`; anything else is mutable. */
export const REF_RE = new RegExp(
  `^lab:(${REF_KINDS.join("|")}):(${UUID}):(${UUID})@sha256:([0-9a-f]{64})$`);

export const attemptKey = (runId: string, caseId: string, attempt: number): string =>
  `attempt:${runId}:${caseId}:${attempt}`;
export const submitKey = (externalRunId: string): string => `submit:${externalRunId}`;
const runKey = (runId: string): string => `run:${runId}`;

// --- the generic walkers (same order and semantics as records.py) ---------------------------
/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;

function refs(node: Json, out: Json[] = []): Json[] {
  if (Array.isArray(node)) node.forEach((item) => refs(item, out));
  else if (node !== null && typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      if (key.endsWith("_ref") && value !== null) out.push(value);
      else if (key.endsWith("_refs") && Array.isArray(value)) out.push(...value);
      else refs(value, out);
    }
  }
  return out;
}

function mixed(node: Json): boolean {
  if (Array.isArray(node)) return node.some(mixed);
  if (node === null || typeof node !== "object") return false;
  const units = Object.values(node)
    .filter((v: Json) => v !== null && typeof v === "object" && !Array.isArray(v) && "unit" in v)
    .map((v: Json) => v.unit);
  return units.some((u) => u !== units[0]) || Object.values(node).some(mixed);
}

// --- shapes: required keys, optional (nullable) keys, then the record's own rule ------------
type Check = (v: Json) => boolean;
const re = (r: RegExp): Check => (v) => typeof v === "string" && r.test(v);
const str: Check = (v) => typeof v === "string";
const text: Check = (v) => typeof v === "string" && v.length > 0;
const uuid = re(new RegExp(`^${UUID}$`));
const ts = re(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/);
const sha = re(/^sha256:[0-9a-f]{64}$/);
const bool: Check = (v) => typeof v === "boolean";
const int = (min: number, max = Infinity): Check => (v) => Number.isInteger(v) && v >= min && v <= max;
const oneOf = (...values: readonly string[]): Check => (v) => values.includes(v);
const obj: Check = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const strMap: Check = (v) => obj(v) && Object.values(v).every(str);
const listOf = (check: Check, min = 0): Check => (v) => Array.isArray(v) && v.length >= min && v.every(check);
const refOf = (...kinds: RefKind[]): Check => (v) => {
  const match = typeof v === "string" ? REF_RE.exec(v) : null;
  return match !== null && (kinds as string[]).includes(match[1]);
};
const state = (kind: string): Check => oneOf(...statesOf(kind));

function shape(required: Record<string, Check>, optional: Record<string, Check> = {},
               rule: (v: Json) => boolean = () => true): Check {
  return (v) => obj(v)
    && Object.entries(required).every(([key, check]) => key in v && check(v[key]))
    && Object.keys(v).every((key) => key in required
      || (key in optional && (v[key] === null || optional[key](v[key]))))
    && rule(v);
}

const record = (schema: Schema, fields: Record<string, Check>, optional: Record<string, Check> = {},
                rule?: (v: Json) => boolean): Check =>
  shape({ schema: oneOf(schema), provider_org_id: uuid, ...fields }, optional, rule);

const units = (value: string): bigint => BigInt(value.replace(".", ""));
const amount = shape({ unit: oneOf(...LAB_UNITS), value: re(/^(0|[1-9][0-9]{0,11})\.[0-9]{8}$/) });
const budget = shape({ limit: amount, reserved: amount }, { payer_ref: refOf("payer") },
  (b) => (b.limit.unit !== "PROVIDER_USD" || (b.payer_ref ?? null) !== null)
    && units(b.reserved.value) <= units(b.limit.value));

const sample = shape({
  sample_id: uuid, modality: oneOf(...MODALITIES), source_ref: refOf("source"),
  grant_ref: refOf("grant"), content_digest: sha, group_key: text,
}, { duration_ms: int(-Infinity) }, (s) => {
  const duration = s.duration_ms ?? null;
  return (s.modality === "finite_video") === (duration !== null)
    && (duration === null || (duration >= 1 && duration <= MAX_VIDEO_MS));
});

function splitsHold(m: Json): boolean {
  if ((m.derivation === "derive") !== (m.parent_refs.length > 0)) return false;
  const ids: string[] = m.samples.map((s: Json) => s.sample_id);
  const placed: string[] = [...m.splits.train, ...m.splits.validation, ...m.splits.holdout];
  if (new Set(ids).size !== ids.length
      || JSON.stringify([...placed].sort()) !== JSON.stringify([...ids].sort())) return false;
  const splitOf = new Map<string, string>();
  for (const name of ["train", "validation", "holdout"]) for (const id of m.splits[name]) splitOf.set(id, name);
  const groups = new Map<string, string>();
  for (const s of m.samples) {
    const split = splitOf.get(s.sample_id) as string;
    if ((groups.get(s.group_key) ?? split) !== split) return false;
    groups.set(s.group_key, split);
  }
  return true;
}

const tool = shape({
  name: re(/^[a-z][a-z0-9_]{0,63}$/), effect: oneOf("read_only", "network_mutation", "actuator"),
  input_schema: obj,
});
const candidate = shape({ serving_ref: refOf("serving"), weight_bp: int(0, 10_000) });
const objectId = (ref: string): string => (REF_RE.exec(ref) as RegExpExecArray)[3];

const SHAPES: Record<Schema, Check> = {
  "lab.dataset_manifest.1": record("lab.dataset_manifest.1", {
    dataset_id: uuid, version: int(1), created_at: ts, derivation: oneOf("import", "derive"),
    parent_refs: listOf(refOf("dataset")), samples: listOf(sample, 1),
    splits: shape({ train: listOf(uuid), validation: listOf(uuid), holdout: listOf(uuid) }),
  }, {}, splitsHold),
  "lab.harness_revision.1": record("lab.harness_revision.1", {
    harness_id: uuid, version: int(1), created_at: ts, adapter: oneOf(...MODALITIES),
    prompt_template: text, processor_profile: text, input_mapping: strMap, tools: listOf(tool),
  }, { reference_output: obj }),
  "lab.eval_run.1": record("lab.eval_run.1", {
    run_id: uuid, created_at: ts, dataset_ref: refOf("dataset"), harness_ref: refOf("harness"),
    serving_ref: refOf("serving"), evaluator_ref: refOf("evaluator"), seed: int(0),
    environment: oneOf("dev"), max_cases: int(1), state: state("run"), idempotency_key: str,
    budgets: listOf(budget),
  }, {}, (r) => r.idempotency_key === runKey(r.run_id)
    && new Set(r.budgets.map((b: Json) => b.limit.unit)).size === r.budgets.length),
  "lab.attempt.1": record("lab.attempt.1", {
    run_ref: refOf("run"), case_id: uuid, attempt: int(1), idempotency_key: str,
    state: state("attempt"),
  }, {}, (a) => a.idempotency_key === attemptKey(objectId(a.run_ref), a.case_id, a.attempt)),
  "lab.checkpoint.1": record("lab.checkpoint.1", {
    checkpoint_id: uuid, external_run_ref: refOf("external_run"), artifact_digest: sha,
    state: state("checkpoint"), received_at: ts,
  }),
  "lab.annotation.1": record("lab.annotation.1", {
    annotation_id: uuid, dataset_ref: refOf("dataset"), sample_id: uuid,
    method: oneOf("human", "synthetic", "imported"), method_version: text,
    rubric_ref: refOf("rubric"), evidence_ref: refOf("source"), label: obj, ground_truth: bool,
    state: state("annotation"),
  }, { reviewer_id: uuid }, (a) => (a.method !== "human" || (a.reviewer_id ?? null) !== null)
    && !(a.method === "synthetic" && a.ground_truth)),
  "lab.external_run.1": record("lab.external_run.1", {
    external_run_id: uuid, purpose: oneOf("external_judging", "training"), connector: text,
    dataset_ref: refOf("dataset"), submit_key: str, state: state("external_run"), budget,
  }, {}, (e) => e.submit_key === submitKey(e.external_run_id) && e.budget.limit.unit === "PROVIDER_USD"),
  "lab.rollout_policy.1": record("lab.rollout_policy.1", {
    policy_id: uuid, version: int(1), created_at: ts, endpoint_id: uuid,
    baseline_ref: refOf("serving"), mode: oneOf("off", "shadow", "canary"),
    cohort: oneOf("account", "session"), candidates: listOf(candidate),
  }, {}, (p) => {
    const total = p.candidates.reduce((sum: number, c: Json) => sum + c.weight_bp, 0);
    return total <= 10_000 && (p.mode === "canary" || total === 0);
  }),
  "lab.rollout_assignment.1": record("lab.rollout_assignment.1", {
    policy_ref: refOf("policy"), request_id: uuid, cohort_digest: sha,
    serving_ref: refOf("serving"), pinned_by: oneOf("cohort", "explicit"),
  }),
  "lab.rollout_decision.1": record("lab.rollout_decision.1", {
    policy_ref: refOf("policy"), decision: oneOf("expand", "hold", "rollback"),
    evidence_refs: listOf(refOf("run")), decided_by: uuid, decided_at: ts,
  }, {}, (d) => d.decision !== "expand" || d.evidence_refs.length > 0),
  "lab.optimization_variant.1": record("lab.optimization_variant.1", {
    variant_id: uuid, base_serving_ref: refOf("serving"), variant_serving_ref: refOf("serving"),
    changes: listOf(text, 1),
  }, {}, (v) => v.base_serving_ref !== v.variant_serving_ref),
};

/** null for a valid Lab record, else the first refusal reason in `REASONS` order. */
export function validate(payload: unknown): Reason | null {
  const p = payload as Json;
  const schema = p !== null && typeof p === "object" && !Array.isArray(p) ? p.schema : undefined;
  if (typeof schema !== "string" || !(SCHEMAS as readonly string[]).includes(schema)) return "unknown_schema";
  const found = refs(p);
  if (!found.every((ref) => typeof ref === "string" && REF_RE.test(ref))) return "mutable_ref";
  if (!found.every((ref) => (REF_RE.exec(ref) as RegExpExecArray)[2] === p.provider_org_id)) {
    return "cross_provider_ref";
  }
  if (mixed(p)) return "mixed_units";
  return SHAPES[schema as Schema](p) ? null : "invalid";
}
