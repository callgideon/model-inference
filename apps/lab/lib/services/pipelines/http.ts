// WR-P4-1: the pipelines port over infrx-api's `/lab/v1/pipelines` (LAB_PIPELINES), as the signed-in
// user: the credential is the session's own access token (none: nothing is sent) and the provider is the
// actor's, sent as `provider_org_id`; the route re-derives both. The route speaks snake_case (P1/P3's
// records), the port camelCase: keys are renamed both ways, no value is touched; the bundle is the
// route's JSON as text. Lists come as `{data}`; every refusal is the route's status mapped to the port's
// reason (410: gone); an answer holding one record the pages cannot read is unavailable (fails closed).
import { bool, list as many, nul, num, obj, oneOf, str, type Check } from "../evaluation/shape.ts";
import { ADAPTERS, IMPORT_REFUSALS, TEACHER_CHUNK_STATES, type Actor, type PipelinesPort, type Refusal, type Result } from "./port.ts";

const REASONS: Record<number, Refusal> = { 401: "denied", 403: "denied", 404: "not_found", 409: "conflict", 410: "gone", 422: "invalid" };
export type HttpOptions = { baseUrl: string; token: () => Promise<string | null>; fetch?: typeof fetch };
type Answer = (payload: { data?: unknown }) => unknown;

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

const rename = (to: (key: string) => string) => {
  const deep = (value: unknown): unknown =>
    Array.isArray(value) ? value.map(deep)
      : value !== null && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([k, v]) => [to(k), deep(v)])) : value;
  return deep;
};
/** Deep key renames; these records and inputs carry no data-keyed maps. */
export const camel = rename((k) => k.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase()));
export const snake = rename((k) => k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`));
const list: Answer = (p) => camel(p.data);
const none: Answer = () => null;
const any: Check = () => true;

export function httpPipelines({ baseUrl, token, fetch: send = fetch }: HttpOptions): PipelinesPort {
  const root = `${baseUrl.replace(/\/+$/, "")}/lab/v1/pipelines`;
  async function call<T>(actor: Actor, method: "GET" | "POST", path: string, read: Answer, readable: Check, body?: unknown, query = ""): Promise<Result<T>> {
    const bearer = await token().catch(() => null);
    if (!bearer) return { ok: false, reason: "unavailable" }; // no session: nothing is sent
    const url = `${root}/${path}?provider_org_id=${encodeURIComponent(actor.providerId)}${query}`;
    const headers: Record<string, string> = { authorization: `Bearer ${bearer}` };
    if (body !== undefined) headers["content-type"] = "application/json";
    try {
      const response = await send(url, { method, headers, cache: "no-store", body: body === undefined ? undefined : JSON.stringify(body) });
      if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };
      const value = read(await response.json());
      return readable(value) ? { ok: true, value: value as T } : { ok: false, reason: "unavailable" }; // an unreadable record
    } catch {
      return { ok: false, reason: "unavailable" }; // transport or an unparseable answer
    }
  }
  const get = <T>(actor: Actor, path: string, readable: Check, read: Answer = list, query = "") => call<T>(actor, "GET", path, read, readable, undefined, query);
  const post = <T>(actor: Actor, path: string, readable: Check, body?: unknown, read: Answer = camel) => call<T>(actor, "POST", path, read, readable, body);
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
