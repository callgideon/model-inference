"use server";
// P4 pipeline actions. The provider and role are the session's (requireProviderWorkspace), never the
// form's; the payer and dataset must be this provider's; every write-once id (import, export, run,
// checkpoint) is minted when the form renders, so a double submit repeats it and the service answers
// the same record; the connector is always the manual bundle. A missing capability or malformed value
// is refused before the service is asked; success is a plain return to the page, which re-reads.
import { redirect } from "next/navigation";
import type { Membership } from "../../auth/access.ts";
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { DIGEST_RE as DIGEST, USD_RE as USD, UUID, UUID_RE as ID } from "../shapes.ts";
import { ADAPTERS, EXPORT_FORMATS, holds, pipelinesPort, type Actor, type Capability, type Refusal, type Result } from "./port.ts";

const SAMPLE = /^[A-Za-z0-9._:-]{1,200}$/;
const MODEL = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const MAX_EXPORT_TTL_S = 604_800; // N2's bound, as P1 enforces it
const MAX_TEXT = 1_000_000;
const MAX_CHUNK = 200; // J1's scan bound, as P2 enforces it

const text = (data: FormData, name: string) => {
  const v = data.get(name);
  return typeof v === "string" ? v : null;
};
const field = (data: FormData, name: string, shape: RegExp) => {
  const v = text(data, name);
  return v !== null && shape.test(v) ? v : null;
};
/** A ref of this provider's own (`lab:<kind>:<provider>:<uuid>@sha256:…`); another provider's is not one here. */
const own = (data: FormData, name: string, kind: string, w: Membership) =>
  field(data, name, new RegExp(`^lab:${kind}:${w.providerId}:${UUID}@sha256:[0-9a-f]{64}$`));
const json = (data: FormData, name: string) => {
  const v = text(data, name);
  if (v === null || v.length > MAX_TEXT) return null;
  try {
    JSON.parse(v);
    return v;
  } catch {
    return null;
  }
};
const oneOf = <T extends string>(data: FormData, name: string, values: readonly T[]) => {
  const v = text(data, name);
  return values.includes(v as T) ? (v as T) : null;
};
const actor = (w: Membership): Actor => ({ providerId: w.providerId, role: w.role });

/** Refused here (role, then shape) or the service's answer; either way the page re-reads the records. */
async function land(page: string, w: Membership, capability: Capability, valid: boolean, call: () => Promise<Result<unknown>>): Promise<never> {
  const refused: Refusal | null = !holds(w.role, capability) ? "denied" : !valid ? "invalid" : null;
  const result = refused === null ? await call() : { ok: false as const, reason: refused };
  redirect(result.ok ? page : `${page}${page.includes("?") ? "&" : "?"}refused=${result.reason}`);
}
const labelsPage = (dataset: string | null) => (dataset === null ? "/annotations" : `/annotations?dataset=${encodeURIComponent(dataset)}`);

export async function importLabels(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const importId = field(data, "importId", ID);
  const datasetRef = own(data, "datasetRef", "dataset", w);
  const rubricRef = own(data, "rubricRef", "rubric", w);
  const rows = text(data, "rows");
  const valid = importId !== null && datasetRef !== null && rubricRef !== null && rows !== null && rows.length <= MAX_TEXT;
  await land(labelsPage(datasetRef), w, "run_evaluation", valid, () =>
    pipelinesPort().importLabels(actor(w), { importId: importId!, datasetRef: datasetRef!, rubricRef: rubricRef!, rows: rows! }));
}

export async function assignReviewer(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const datasetRef = own(data, "datasetRef", "dataset", w);
  const sampleId = field(data, "sampleId", SAMPLE);
  const reviewerId = field(data, "reviewerId", ID);
  const rubricRef = own(data, "rubricRef", "rubric", w);
  await land(labelsPage(datasetRef), w, "manage_members", ![datasetRef, sampleId, reviewerId, rubricRef].includes(null), () =>
    pipelinesPort().assign(actor(w), { datasetRef: datasetRef!, sampleId: sampleId!, reviewerId: reviewerId!, rubricRef: rubricRef! }));
}

/** Accept or reject; a correction (JSON) only rejects the label it corrects. */
export async function reviewLabel(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const datasetRef = own(data, "datasetRef", "dataset", w);
  const annotationRef = own(data, "annotationRef", "annotation", w);
  const decision = oneOf(data, "decision", ["accepted", "rejected"] as const);
  const rubricRef = own(data, "rubricRef", "rubric", w);
  const raw = text(data, "correction") ?? "";
  const correction = raw === "" ? null : json(data, "correction");
  const valid = ![datasetRef, annotationRef, decision, rubricRef].includes(null) && (raw === "" || (correction !== null && decision === "rejected"));
  await land(labelsPage(datasetRef), w, "run_evaluation", valid, () =>
    pipelinesPort().review(actor(w), { datasetRef: datasetRef!, annotationRef: annotationRef!, decision: decision!, rubricRef: rubricRef!, correction }));
}

export async function adjudicateSample(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const datasetRef = own(data, "datasetRef", "dataset", w);
  const sampleId = field(data, "sampleId", SAMPLE);
  const value = json(data, "value");
  const rubricRef = own(data, "rubricRef", "rubric", w);
  await land(labelsPage(datasetRef), w, "run_evaluation", ![datasetRef, sampleId, value, rubricRef].includes(null), () =>
    pipelinesPort().adjudicate(actor(w), { datasetRef: datasetRef!, sampleId: sampleId!, value: value!, rubricRef: rubricRef! }));
}

export async function exportLabels(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const exportId = field(data, "exportId", ID);
  const datasetRef = own(data, "datasetRef", "dataset", w);
  const adapter = oneOf(data, "adapter", ADAPTERS);
  const ttl = field(data, "ttlS", /^[1-9][0-9]{0,6}$/);
  const ttlS = ttl === null ? 0 : Number(ttl);
  const valid = exportId !== null && datasetRef !== null && adapter !== null && ttlS >= 1 && ttlS <= MAX_EXPORT_TTL_S;
  await land(labelsPage(datasetRef), w, "run_evaluation", valid, () =>
    pipelinesPort().exportLabels(actor(w), { exportId: exportId!, datasetRef: datasetRef!, adapter: adapter!, ttlS }));
}

/** The training bundle, as the manual connector: no connector is read from the form (P-11). */
export async function prepareTraining(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const externalRunId = field(data, "externalRunId", ID);
  const datasetRef = own(data, "datasetRef", "dataset", w);
  const exportFormat = oneOf(data, "exportFormat", EXPORT_FORMATS);
  const exportId = field(data, "exportId", ID);
  const objective = oneOf(data, "objective", ["sft", "preference"] as const);
  const adaptation = oneOf(data, "adaptation", ["full", "lora"] as const);
  const baseModel = field(data, "baseModel", MODEL);
  const payerRef = own(data, "payerRef", "payer", w);
  const limitUsd = field(data, "limitUsd", USD);
  const valid = ![externalRunId, datasetRef, exportFormat, exportId, objective, adaptation, baseModel, payerRef, limitUsd].includes(null);
  await land("/training", w, "run_evaluation", valid, () =>
    pipelinesPort().prepare(actor(w), {
      externalRunId: externalRunId!, datasetRef: datasetRef!, exportFormat: exportFormat!, exportId: exportId!,
      config: { objective: objective!, adaptation: adaptation!, baseModel: baseModel! }, payerRef: payerRef!, limitUsd: limitUsd!,
    }));
}

/** submit (at most once per run; an ambiguous one is only looked up), finish (manual), cancel. */
export async function runAction(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const op = oneOf(data, "op", ["submit", "finish", "cancel"] as const);
  const id = field(data, "externalRunId", ID);
  await land("/training", w, "run_evaluation", op !== null && id !== null, () => pipelinesPort()[op!](actor(w), id!));
}

export async function importCheckpoint(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const externalRunId = field(data, "externalRunId", ID);
  const checkpointId = field(data, "checkpointId", ID);
  const artifactKey = text(data, "artifactKey");
  const artifactDigest = field(data, "artifactDigest", DIGEST);
  const under = externalRunId !== null && artifactKey !== null && artifactKey.startsWith(`lab/${w.providerId}/training/${externalRunId}/`)
    && /^[A-Za-z0-9._/-]{1,512}$/.test(artifactKey) && !artifactKey.includes("..");
  await land("/training", w, "run_evaluation", under && checkpointId !== null && artifactDigest !== null, () =>
    pipelinesPort().importCheckpoint(actor(w), { externalRunId: externalRunId!, checkpointId: checkpointId!, artifactKey: artifactKey!, artifactDigest: artifactDigest! }));
}

/** An eligible candidate only: not public, not promoted. The service re-checks the held-out result. */
export async function approveCheckpoint(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const externalRunId = field(data, "externalRunId", ID);
  const checkpointId = field(data, "checkpointId", ID);
  await land("/training", w, "run_evaluation", externalRunId !== null && checkpointId !== null, () =>
    pipelinesPort().approve(actor(w), { externalRunId: externalRunId!, checkpointId: checkpointId! }));
}

/** P4.b: a teacher batch's dry run. The batch id is the form's (minted at render), so a double submit is one
 *  batch; the budget is PROVIDER_USD with this provider's own payer; nothing here sends or approves. */
export async function planTeachers(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const batchId = field(data, "batchId", ID);
  const datasetRef = own(data, "datasetRef", "dataset", w);
  const rubricRef = own(data, "rubricRef", "rubric", w);
  const teacherModel = field(data, "teacherModel", MODEL);
  const promptVersion = field(data, "promptVersion", MODEL);
  const payerRef = own(data, "payerRef", "payer", w);
  const budgetUsd = field(data, "budgetUsd", USD);
  const chunk = field(data, "chunkSize", /^[1-9][0-9]{0,2}$/);
  const chunkSize = chunk === null ? 0 : Number(chunk);
  const valid = ![batchId, datasetRef, rubricRef, teacherModel, promptVersion, payerRef, budgetUsd].includes(null) && chunkSize <= MAX_CHUNK && chunkSize >= 1;
  await land("/training", w, "run_evaluation", valid, () =>
    pipelinesPort().planTeachers(actor(w), {
      batchId: batchId!, datasetRef: datasetRef!, rubricRef: rubricRef!, teacherModel: teacherModel!, promptVersion: promptVersion!,
      payerRef: payerRef!, budgetUsd: budgetUsd!, chunkSize,
    }));
}

/** The live submit, an administrator's only; the service re-checks the budget and resumes, never resends. */
export async function approveTeachers(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const batchId = field(data, "batchId", ID);
  await land("/training", w, "manage_members", batchId !== null, () => pipelinesPort().approveTeachers(actor(w), batchId!));
}
