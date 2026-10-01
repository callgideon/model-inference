// WR-P4-1: the pipelines port over infrx-api's `/lab/v1/pipelines` (LAB_PIPELINES), as the signed-in
// user: the credential is the session's own access token (none: nothing is sent) and the provider is the
// actor's, sent as `provider_org_id`; the route re-derives both. The route speaks snake_case (P1/P3's
// records), the port camelCase: keys are renamed both ways, no value is touched; the bundle is the
// route's JSON as text. Lists come as `{data}`; every refusal is the route's status mapped to the port's
// reason (410: gone); an answer holding one record the pages cannot read is unavailable (fails closed).
// The transport, the renames and the row checks are the shared ones (../http.ts).
import { bool, camel, labClient, list as many, nul, num, obj, oneOf, REASONS, snake, str, type Answer, type Check, type HttpOptions } from "../http.ts";
import { ADAPTERS, IMPORT_REFUSALS, TEACHER_CHUNK_STATES, type PipelinesPort } from "./port.ts";

export type { HttpOptions };

/** The port's records (camelCase, after the rename) as the pages read them. port.ts loads this module
 *  (through server.ts) before its own constants exist, so those are read at call time. */
const among = (values: () => readonly unknown[]): Check => (v) => values().includes(v);
const METHOD = oneOf("imported", "synthetic", "human");
const LABEL = obj({
  annotationRef: str, sampleId: str, method: METHOD, groundTruth: bool,
  state: oneOf("submitted", "accepted", "rejected", "superseded"), value: str, reviewerId: nul(str),
});
const DISAGREEMENT = obj({ sampleId: str, annotationRefs: many(str) });
const RECEIPT = obj({ importId: str, datasetRef: str, accepted: num, rejected: many(obj({ row: num, reason: among(() => IMPORT_REFUSALS) })) });
const EXPORT = obj({
  exportId: str, datasetRef: str, adapter: among(() => ADAPTERS), items: num, expiresAt: str,
  lineage: many(obj({ sampleId: str, labelRefs: many(str), methods: many(METHOD) })), omitted: many(obj({ sampleId: str, reason: str })),
});
const RUN = obj({
  externalRunId: str, runRef: str, connector: str, datasetRef: str,
  state: oneOf("prepared", "submitting", "submitted", "ambiguous", "completed", "failed", "cancelled"),
  export: obj({ format: str, exportId: str, sha256: str }),
  config: obj({ objective: oneOf("sft", "preference"), adaptation: oneOf("full", "lora"), baseModel: str }),
  train: num, dev: num, omitted: num, holdout: obj({ size: num, sha256: str }), payerRef: str,
  limitUsd: str, reservedUsd: str, settled: bool, costUsd: nul(str), reason: nul(str),
});
const CHECKPOINT = obj({
  checkpointId: str, externalRunId: str, artifactDigest: str, state: oneOf("rejected", "validated"), reason: nul(str), eligible: bool,
  evaluation: nul(obj({ runRef: str, state: oneOf("queued", "running", "succeeded", "failed"), split: str, holdoutSha256: str })),
});
const TEACHER = obj({
  batchId: str, datasetRef: str, rubricRef: str, teacherModel: str, promptVersion: str, payerRef: str, budgetUsd: str, chunkSize: num,
  requestedBy: str, priceVersion: nul(str), ceilingUsd: nul(str), withinBudget: bool, holdout: num, notPermitted: num,
  approval: nul(obj({ approvedBy: str, approvedAt: str })),
  chunks: many(obj({
    runId: str, samples: num, ceilingUsd: nul(str), state: among(() => TEACHER_CHUNK_STATES), reservedUsd: nul(str), costUsd: nul(str), sent: num,
    failures: many(obj({ sampleId: str, reason: str })),
  })),
});

const list: Answer = (p) => camel(p.data);
const none: Answer = () => null;
const any: Check = () => true;

export function httpPipelines(options: HttpOptions): PipelinesPort {
  const { get, post } = labClient(options, "/lab/v1/pipelines", { reasons: { ...REASONS, 410: "gone" as const }, rename: camel });
  const set = (datasetRef: string) => `&dataset_ref=${encodeURIComponent(datasetRef)}`;
  const run = (id: string) => `training-runs/${encodeURIComponent(id)}`;
  return {
    labels: (actor, datasetRef) => get(actor, "labels", many(LABEL), list, set(datasetRef)),
    disagreements: (actor, datasetRef) => get(actor, "disagreements", many(DISAGREEMENT), list, set(datasetRef)),
    imports: (actor) => get(actor, "label-imports", many(RECEIPT)),
    exports: (actor) => get(actor, "label-exports", many(EXPORT)),
    runs: (actor) => get(actor, "training-runs", many(RUN)),
    checkpoints: (actor) => get(actor, "checkpoints", many(CHECKPOINT)),
    bundle: (actor, id) => get(actor, `${run(id)}/bundle`, str, (p) => (obj({})(p) ? JSON.stringify(p) : null)),
    importLabels: (actor, input) => post(actor, "label-imports", RECEIPT, snake(input)),
    assign: (actor, input) => post(actor, "assignments", any, snake(input), none),
    review: (actor, input) => post(actor, "reviews", any, snake(input), none),
    adjudicate: (actor, input) => post(actor, "adjudications", any, snake(input), none),
    exportLabels: (actor, input) => post(actor, "label-exports", EXPORT, snake(input)),
    prepare: (actor, { exportFormat, exportId, config, limitUsd, ...rest }) => post(actor, "training-runs", RUN, {
      ...(snake(rest) as object), export: { format: exportFormat, export_id: exportId }, config: snake(config), limit: limitUsd }),
    submit: (actor, id) => post(actor, `${run(id)}/submit`, RUN),
    finish: (actor, id) => post(actor, `${run(id)}/finish`, RUN),
    cancel: (actor, id) => post(actor, `${run(id)}/cancel`, RUN),
    importCheckpoint: (actor, input) => post(actor, "checkpoints", CHECKPOINT, snake(input)),
    approve: (actor, { externalRunId, checkpointId }) =>
      post(actor, `checkpoints/${encodeURIComponent(checkpointId)}/approve`, CHECKPOINT, { external_run_id: externalRunId }),
    teacherBatches: (actor) => get(actor, "teacher-batches", many(TEACHER)),
    planTeachers: (actor, input) => post(actor, "teacher-batches", TEACHER, snake(input)),
    approveTeachers: (actor, id) => post(actor, `teacher-batches/${encodeURIComponent(id)}/approve`, TEACHER),
  };
}
