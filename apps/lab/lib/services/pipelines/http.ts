// WR-P4-1: the pipelines port over infrx-api's `/lab/v1/pipelines` (LAB_PIPELINES), as the signed-in
// user: the credential is the session's own access token and the provider is the actor's, sent as
// `provider_org_id`; the route re-derives both. The route speaks snake_case (P1/P3's records), the port
// camelCase: keys are renamed both ways, no value is touched; the bundle is the route's JSON as text.
// Lists come as `{data}`; every refusal is the route's status mapped to the port's reason (410: gone).
import type { Actor, PipelinesPort, Refusal, Result } from "./port.ts";

const REASONS: Record<number, Refusal> = { 401: "denied", 403: "denied", 404: "not_found", 409: "conflict", 410: "gone", 422: "invalid" };
export type HttpOptions = { baseUrl: string; token: string; fetch?: typeof fetch };
type Answer = (payload: { data?: unknown }) => unknown;

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

export function httpPipelines({ baseUrl, token, fetch: send = fetch }: HttpOptions): PipelinesPort {
  const root = `${baseUrl.replace(/\/+$/, "")}/lab/v1/pipelines`;
  async function call<T>(actor: Actor, method: "GET" | "POST", path: string, read: Answer, body?: unknown, query = ""): Promise<Result<T>> {
    const url = `${root}/${path}?provider_org_id=${encodeURIComponent(actor.providerId)}${query}`;
    const headers: Record<string, string> = { authorization: `Bearer ${token}` };
    if (body !== undefined) headers["content-type"] = "application/json";
    try {
      const response = await send(url, { method, headers, cache: "no-store", body: body === undefined ? undefined : JSON.stringify(body) });
      if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };
      return { ok: true, value: read(await response.json()) as T };
    } catch {
      return { ok: false, reason: "unavailable" }; // transport or an unreadable answer
    }
  }
  const get = <T>(actor: Actor, path: string, read: Answer = list, query = "") => call<T>(actor, "GET", path, read, undefined, query);
  const post = <T>(actor: Actor, path: string, body?: unknown, read: Answer = camel) => call<T>(actor, "POST", path, read, body);
  const set = (datasetRef: string) => `&dataset_ref=${encodeURIComponent(datasetRef)}`;
  const run = (id: string) => `training-runs/${encodeURIComponent(id)}`;
  return {
    labels: (actor, datasetRef) => get(actor, "labels", list, set(datasetRef)),
    disagreements: (actor, datasetRef) => get(actor, "disagreements", list, set(datasetRef)),
    imports: (actor) => get(actor, "label-imports"),
    exports: (actor) => get(actor, "label-exports"),
    runs: (actor) => get(actor, "training-runs"),
    checkpoints: (actor) => get(actor, "checkpoints"),
    bundle: (actor, id) => get(actor, `${run(id)}/bundle`, (p) => JSON.stringify(p)),
    importLabels: (actor, input) => post(actor, "label-imports", snake(input)),
    assign: (actor, input) => post(actor, "assignments", snake(input), none),
    review: (actor, input) => post(actor, "reviews", snake(input), none),
    adjudicate: (actor, input) => post(actor, "adjudications", snake(input), none),
    exportLabels: (actor, input) => post(actor, "label-exports", snake(input)),
    prepare: (actor, { exportFormat, exportId, config, limitUsd, ...rest }) => post(actor, "training-runs", {
      ...(snake(rest) as object), export: { format: exportFormat, export_id: exportId }, config: snake(config), limit: limitUsd }),
    submit: (actor, id) => post(actor, `${run(id)}/submit`),
    finish: (actor, id) => post(actor, `${run(id)}/finish`),
    cancel: (actor, id) => post(actor, `${run(id)}/cancel`),
    importCheckpoint: (actor, input) => post(actor, "checkpoints", snake(input)),
    approve: (actor, { externalRunId, checkpointId }) =>
      post(actor, `checkpoints/${encodeURIComponent(checkpointId)}/approve`, { external_run_id: externalRunId }),
  };
}
