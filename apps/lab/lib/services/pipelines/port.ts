// P4: the Lab's view of P1 (infrx/pipelines/annotations) and P3 (infrx/pipelines/training) through
// lab-api's LAB_PIPELINES route (WR-P4-1, `/lab/v1/pipelines`). With LAB_PIPELINES_API_URL set the port
// is the HTTP adapter (server.ts); unset it is "unavailable" (fails closed), or, only outside production
// and only when asked for, the labelled preview fake. The route derives the user from the forwarded session (LAB-AUTH) and
// re-checks every call; the Lab only ever names the session's workspace.
import type { Role } from "../../auth/access.ts";
import { FakePipelines } from "./fake.ts";
import { labPipelines } from "./server.ts";

/** contracts/v2 ROLE_CAPABILITIES: P1/P3 need run_evaluation; assigning a reviewer, manage_members. */
export type Capability = "run_evaluation" | "manage_members";
const CAPABILITIES: Record<Role, readonly Capability[]> = {
  viewer: [],
  developer: ["run_evaluation"],
  administrator: ["run_evaluation", "manage_members"],
};
export const holds = (role: Role, capability: Capability): boolean => CAPABILITIES[role].includes(capability);

export type Actor = { providerId: string; role: Role };

/** P1: an imported pipeline row is `imported` (human) or `synthetic` (model); only a review is `human`. */
export type Method = "imported" | "synthetic" | "human";
export type Label = {
  annotationRef: string;
  sampleId: string;
  method: Method;
  groundTruth: boolean;
  state: "submitted" | "accepted" | "rejected" | "superseded";
  value: string; // the label value as canonical JSON text
  reviewerId: string | null;
};
export type Disagreement = { sampleId: string; annotationRefs: string[] };
export const IMPORT_REFUSALS = ["missing_evidence", "grant_not_current", "forged_ground_truth", "bad_mapping"] as const;
export type ImportReceipt = {
  importId: string;
  datasetRef: string;
  accepted: number;
  rejected: { row: number; reason: (typeof IMPORT_REFUSALS)[number] }[];
};
export const ADAPTERS = ["sft.1", "preference.1"] as const;
export type LabelExport = {
  exportId: string;
  datasetRef: string;
  adapter: (typeof ADAPTERS)[number];
  items: number;
  expiresAt: string;
  lineage: { sampleId: string; labelRefs: string[]; methods: Method[] }[];
  omitted: { sampleId: string; reason: string }[];
};

/** P3: F3's external_run machine. The manual bundle is the only connector the Lab offers (P-11). */
export const MANUAL = "manual-bundle";
export const EXPORT_FORMATS = ["infrx.label_export.1", "infrx.dataset_export.1"] as const;
export type TrainingConfig = { objective: "sft" | "preference"; adaptation: "full" | "lora"; baseModel: string };
export type TrainingRun = {
  externalRunId: string;
  runRef: string;
  connector: string;
  state: "prepared" | "submitting" | "submitted" | "ambiguous" | "completed" | "failed" | "cancelled";
  datasetRef: string;
  export: { format: string; exportId: string; sha256: string };
  config: TrainingConfig;
  train: number;
  dev: number;
  omitted: number;
  holdout: { size: number; sha256: string };
  payerRef: string;
  limitUsd: string; // PROVIDER_USD, exact decimal strings; never CREDIT
  reservedUsd: string;
  settled: boolean;
  costUsd: string | null; // settled and null: the provider reported none (unknown, never estimated)
  reason: string | null;
};
export type Checkpoint = {
  checkpointId: string;
  externalRunId: string;
  artifactDigest: string;
  state: "rejected" | "validated";
  reason: string | null;
  evaluation: { runRef: string; state: "queued" | "running" | "succeeded" | "failed"; split: string; holdoutSha256: string } | null;
  eligible: boolean;
};

export const REFUSALS = ["denied", "not_found", "invalid", "conflict", "gone", "unavailable"] as const;
export type Refusal = (typeof REFUSALS)[number];
export type Result<T> = { ok: true; value: T } | { ok: false; reason: Refusal };

export type ImportInput = { importId: string; datasetRef: string; rubricRef: string; rows: string };
export type AssignInput = { datasetRef: string; sampleId: string; reviewerId: string; rubricRef: string };
export type ReviewInput = { datasetRef: string; annotationRef: string; decision: "accepted" | "rejected"; rubricRef: string; correction: string | null };
export type AdjudicateInput = { datasetRef: string; sampleId: string; value: string; rubricRef: string };
export type ExportInput = { exportId: string; datasetRef: string; adapter: LabelExport["adapter"]; ttlS: number };
export type PrepareInput = {
  externalRunId: string; datasetRef: string; exportFormat: string; exportId: string;
  config: TrainingConfig; payerRef: string; limitUsd: string;
};
export type CheckpointInput = { externalRunId: string; checkpointId: string; artifactKey: string; artifactDigest: string };

export interface PipelinesPort {
  labels(actor: Actor, datasetRef: string): Promise<Result<Label[]>>;
  disagreements(actor: Actor, datasetRef: string): Promise<Result<Disagreement[]>>;
  imports(actor: Actor): Promise<Result<ImportReceipt[]>>;
  exports(actor: Actor): Promise<Result<LabelExport[]>>;
  /** Write-once per importId: a replay is the stored receipt, another body a conflict. */
  importLabels(actor: Actor, input: ImportInput): Promise<Result<ImportReceipt>>;
  assign(actor: Actor, input: AssignInput): Promise<Result<null>>;
  review(actor: Actor, input: ReviewInput): Promise<Result<null>>;
  adjudicate(actor: Actor, input: AdjudicateInput): Promise<Result<null>>;
  exportLabels(actor: Actor, input: ExportInput): Promise<Result<LabelExport>>;
  runs(actor: Actor): Promise<Result<TrainingRun[]>>;
  checkpoints(actor: Actor): Promise<Result<Checkpoint[]>>;
  /** The bundle JSON the provider trains from. */
  bundle(actor: Actor, externalRunId: string): Promise<Result<string>>;
  /** Write-once per externalRunId; always the manual bundle. */
  prepare(actor: Actor, input: PrepareInput): Promise<Result<TrainingRun>>;
  /** At most one submission per run, ever; an ambiguous one is only looked up. */
  submit(actor: Actor, externalRunId: string): Promise<Result<TrainingRun>>;
  finish(actor: Actor, externalRunId: string): Promise<Result<TrainingRun>>;
  cancel(actor: Actor, externalRunId: string): Promise<Result<TrainingRun>>;
  /** A redelivery (same checkpointId) is the first outcome. */
  importCheckpoint(actor: Actor, input: CheckpointInput): Promise<Result<Checkpoint>>;
  approve(actor: Actor, input: { externalRunId: string; checkpointId: string }): Promise<Result<Checkpoint>>;
}

const down = async () => ({ ok: false, reason: "unavailable" }) as const;
const UNAVAILABLE: PipelinesPort = {
  labels: down, disagreements: down, imports: down, exports: down, importLabels: down, assign: down, review: down,
  adjudicate: down, exportLabels: down, runs: down, checkpoints: down, bundle: down, prepare: down, submit: down,
  finish: down, cancel: down, importCheckpoint: down, approve: down,
};

let preview: FakePipelines | undefined;
export const isPreview = (env: Record<string, string | undefined> = process.env) =>
  env.LAB_PIPELINES_PREVIEW === "1" && env.NODE_ENV !== "production";

export function pipelinesPort(env: Record<string, string | undefined> = process.env): PipelinesPort {
  if (isPreview(env)) return (preview ??= new FakePipelines());
  return labPipelines(env) ?? UNAVAILABLE;
}
