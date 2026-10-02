// L4: rows and copy derived only from control records. Nothing here remembers what a button did.
import { fixedCopy } from "../common.ts";
import { DIGEST_RE } from "../shapes.ts";
import type { Role } from "../../auth/access.ts";
import { holds, REFUSALS, type Aggregate, type Deployment, type Model, type Proposal, type Refusal, type Result } from "./port.ts";

export type Action = "smoke" | "publish";
export type DeploymentRow = {
  id: string; model: string; serving: string; runtime: string; schema: string; rate: string; where: string;
  state: string; smoke: string; pending: string | null; actions: Action[];
};

export function deploymentRows(role: Role, deployments: Deployment[], proposals: Proposal[]): DeploymentRow[] {
  return deployments.map((d) => {
    const pending = proposals.find((p) => p.deploymentRevisionId === d.deploymentRevisionId && p.state === "proposed");
    const live = d.state === "active";
    const actions: Action[] = [];
    if (live && d.environment === "dev" && holds(role, "manage_dev_deployment")) actions.push("smoke");
    if (live && !pending && holds(role, "propose_publication") && d.environment === "dev" && d.smoke === "passed") actions.push("publish"); // a rollback is the operator's (E3L-F3)
    return {
      id: d.deploymentRevisionId, model: d.modelId, serving: `${d.servingVersionId}@${d.revisionLabel}`, runtime: d.runtime,
      schema: d.schemaVersion, rate: d.rateCardVersion ?? "unpriced", where: `${d.environment} · ${d.visibility}`,
      state: d.state, smoke: d.smoke, pending: pending ? `${pending.kind} proposed · awaiting operator approval` : null, actions,
    };
  });
}

export type HealthRow = { deployment: string; window: string; requests: string; errorRate: string; p95: string };

/** Named fields only: anything else a record carries (an identity, content) is never rendered. */
export function healthRows(aggregates: Aggregate[]): HealthRow[] {
  return aggregates.map((a) => ({
    deployment: a.deploymentRevisionId,
    window: `${a.windowStart} – ${a.windowEnd}`,
    requests: String(a.requests),
    errorRate: a.requests > 0 ? `${Math.round((a.errors / a.requests) * 1000) / 10}%` : "—",
    p95: a.p95LatencyMs === null ? "—" : `${a.p95LatencyMs} ms`,
  }));
}

export const REFUSAL_COPY: Record<Refusal, string> = {
  denied: "Your role in this workspace does not allow that.",
  not_found: "That record is not in this workspace.",
  invalid: "Those values were not accepted. Check the digest, name, schema and runtime.",
  conflict: "That record has moved on or already has a pending request; this page shows its current state.",
  unavailable: "Control records could not be read. Nothing is shown until they can be; try again shortly.",
};

/** `?refused=` is anyone's to write: only a known reason's fixed copy is ever shown (common.ts). */
export const refusalCopy = fixedCopy(REFUSALS, REFUSAL_COPY);

// ---- UX-03 L-02 Overview: setup stages, record counts and measured traffic, from the records only.

export type StageState = "done" | "todo" | "unknown";
export type Stage = { title: string; state: StageState; detail: string };
/** The stage badges; `unknown` is a failed read (never "not started"); nothing here is "Ready". */
export const STAGE_BADGE: Record<StageState, { tone: "success" | "neutral" | "warning"; label: string }> = {
  done: { tone: "success", label: "Done" },
  todo: { tone: "neutral", label: "Not done yet" },
  unknown: { tone: "warning", label: "Couldn't check" },
};

/**
 * The three setup stages (03-lab L-02). A registered model revision completes the first; the second
 * stays open until serving readiness is readable here (AP-05) - a recorded smoke never completes it;
 * a publication request completes the third (the operator's decision is a separate fact).
 */
export function setupStages(models: Result<Model[]>, proposals: Result<Proposal[]>): Stage[] {
  const from = <T>(r: Result<T[]>, done: (rows: T[]) => boolean): StageState => (!r.ok ? "unknown" : done(r.value) ? "done" : "todo");
  return [
    {
      title: "Add a model",
      state: from(models, (rows) => rows.length > 0),
      detail: "Create its project, import or upload its files and verify the artifact, then create a serving revision.",
    },
    {
      title: "Verify a private deployment",
      state: "todo",
      detail: "Serving readiness is not available in this view yet. A recorded smoke result does not qualify a real engine.",
    },
    {
      title: "Request publication",
      state: from(proposals, (rows) => rows.some((p) => p.kind === "publish")),
      detail: "An administrator requests it; an infrx operator approves or rejects it.",
    },
  ];
}

export type Count = { label: string; href: string; value: string | null };
/** The overview's counts; `value` null = the read failed (rendered "Not available", never 0). */
export function recordCounts(deployments: Result<Deployment[]>, proposals: Result<Proposal[]>): Count[] {
  const n = <T>(r: Result<T[]>, keep: (row: T) => boolean) => (r.ok ? String(r.value.filter(keep).length) : null);
  return [
    { label: "Registered deployment records", href: "/deployments", value: n(deployments, (d) => d.state === "active") },
    { label: "Public records", href: "/deployments", value: n(deployments, (d) => d.state === "active" && d.visibility === "public") },
    { label: "Pending publication requests", href: "/deployments#publication", value: n(proposals, (p) => p.state === "proposed") },
  ];
}

export type TrafficRow = HealthRow & { measured: boolean };
/** healthRows with L-02's copy: a window without samples is unmeasured, a missing p95 is not available. */
export function trafficRows(aggregates: Aggregate[]): TrafficRow[] {
  return healthRows(aggregates).map((row, i) => ({
    ...row,
    measured: aggregates[i].requests > 0,
    p95: aggregates[i].p95LatencyMs === null ? "Not available" : row.p95,
  }));
}

/** The evidence time: the latest window end measured (not when this page loaded). */
export function observedThrough(aggregates: Aggregate[]): string | null {
  return aggregates.reduce<string | null>((latest, a) => (latest === null || a.windowEnd > latest ? a.windowEnd : latest), null);
}

// ---- UX-03 L-03 Models: the list and "New revision of an imported model" (the legacy registration).

export type ModelRow = { name: string; modelId: string; revision: string; runtime: string; registered: string; artifactDigest: string; schemaVersion: string };
/** A model's display name is its registered name's last segment; no new editable title (L-03). */
const nameOf = (modelId: string) => modelId.split("/").at(-1) ?? modelId;
export const modelRows = (models: Model[]): ModelRow[] =>
  models.map((m) => ({
    name: nameOf(m.modelId), modelId: m.modelId, revision: m.revisionLabel, runtime: m.runtime, registered: m.registeredAt,
    artifactDigest: m.artifactDigest, schemaVersion: m.schemaVersion,
  }));
/** The names a new revision may target: models already imported into this workspace. */
export const importedNames = (models: Model[]): string[] => [...new Set(models.map((m) => nameOf(m.modelId)))].sort();

export const REGISTRATION_FIELDS = ["name", "artifactDigest", "schemaVersion", "runtime"] as const;
export type RegistrationField = (typeof REGISTRATION_FIELDS)[number];
export type RegistrationValues = Record<RegistrationField, string>;
// The control actions' rules (actions.ts NAME/IDENT, shapes.ts DIGEST_RE), restated per field so a
// refusal names its field; ponytail: the IDENT copy goes when actions.ts exports its shapes.
const RULES: Record<RegistrationField, [RegExp, string]> = {
  name: [/^[a-z0-9][a-z0-9-]{0,62}$/, "Use lowercase letters, digits and hyphens, starting with a letter or digit, up to 63 characters."],
  artifactDigest: [DIGEST_RE, "Enter the artifact digest as sha256: followed by 64 lowercase hex characters."],
  schemaVersion: [/^[A-Za-z0-9._:@/+-]{1,200}$/, "Enter a schema identifier: letters, digits and . _ : @ / + -, no spaces."],
  runtime: [/^[A-Za-z0-9._:@/+-]{1,200}$/, "Enter a runtime identifier: letters, digits and . _ : @ / + -, no spaces."],
};
export function registrationErrors(values: RegistrationValues): Partial<Record<RegistrationField, string>> {
  return Object.fromEntries(REGISTRATION_FIELDS.filter((f) => !RULES[f][0].test(values[f])).map((f) => [f, RULES[f][1]]));
}

export type RegistrationOutcome =
  | { kind: "registered"; deployment: Deployment }
  | { kind: "refused"; message: string }
  | { kind: "uncertain"; message: string };
export type RegistrationState = {
  values: RegistrationValues;
  errors: Partial<Record<RegistrationField, string>>;
  outcome: RegistrationOutcome | null;
} | null;
/** The control service's answer as the form shows it. No answer is "not confirmed": registration has no
 *  replay receipt today (L-03), so the person checks the records before trying again. */
export function registrationOutcome(result: Result<Deployment>): RegistrationOutcome {
  if (result.ok) return { kind: "registered", deployment: result.value };
  if (result.reason === "unavailable")
    return { kind: "uncertain", message: "Outcome not confirmed: the control service did not answer. Check Models before trying again; the revision may have been registered." };
  return { kind: "refused", message: REFUSAL_COPY[result.reason] };
}
