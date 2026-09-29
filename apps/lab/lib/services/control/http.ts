// WR-E3L-J: the control port over the Lab control service's `/lab/v1/control` (R186's factory,
// routes/lab_control.py), in rollouts/http.ts's style: the credential is the session's own access token
// (none: nothing is sent), the provider is the actor's (`provider_org_id`; the route re-derives both from
// the session and L2), snake_case keys become the port's camelCase and no value is touched. Lists come as
// `{data}`; a refusal is the route's status mapped to the port's reason; an answer holding one record the
// pages cannot read is unavailable (fails closed).
import { list, nul, num, obj, oneOf, str, type Check } from "../evaluation/shape.ts";
import { camel } from "../rollouts/http.ts";
import type { ControlPort, Refusal, Result, Actor } from "./port.ts";

const REASONS: Record<number, Refusal> = { 401: "denied", 403: "denied", 404: "not_found", 409: "conflict", 422: "invalid" };
export type HttpOptions = { baseUrl: string; token: () => Promise<string | null>; fetch?: typeof fetch };

const MODEL = obj({ modelId: str, revisionLabel: str, artifactDigest: str, schemaVersion: str, runtime: str, registeredAt: str });
const DEPLOYMENT = obj({
  deploymentRevisionId: str, modelId: str, servingVersionId: str, revisionLabel: str, runtime: str, schemaVersion: str,
  rateCardVersion: nul(str), environment: oneOf("dev", "prod"), visibility: oneOf("private", "public"), state: oneOf("active", "retired"),
  smoke: oneOf("none", "passed", "failed"), createdAt: str,
});
const PROPOSAL = obj({
  proposalId: str, kind: oneOf("publish", "rollback"), deploymentRevisionId: str, state: oneOf("proposed", "approved", "rejected"), proposedAt: str, decidedAt: nul(str),
});
const AGGREGATE = obj({ deploymentRevisionId: str, windowStart: str, windowEnd: str, requests: num, errors: num, p95LatencyMs: nul(num) });

export function httpControl({ baseUrl, token, fetch: send = fetch }: HttpOptions): ControlPort {
  const root = `${baseUrl.replace(/\/+$/, "")}/lab/v1/control`;
  async function call<T>(actor: Actor, method: "GET" | "POST", path: string, readable: Check, body?: unknown): Promise<Result<T>> {
    const bearer = await token().catch(() => null);
    if (!bearer) return { ok: false, reason: "unavailable" }; // no session: nothing is sent
    const url = `${root}/${path}?provider_org_id=${encodeURIComponent(actor.providerId)}`;
    const headers: Record<string, string> = { authorization: `Bearer ${bearer}` };
    if (body !== undefined) headers["content-type"] = "application/json";
    try {
      const response = await send(url, { method, headers, cache: "no-store", body: body === undefined ? undefined : JSON.stringify(body) });
      if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };
      const payload = await response.json();
      const value = camel(method === "GET" ? payload.data : payload); // a read is {data}
      return readable(value) ? { ok: true, value: value as T } : { ok: false, reason: "unavailable" }; // an unreadable record
    } catch {
      return { ok: false, reason: "unavailable" }; // transport or an unparseable answer
    }
  }
  return {
    models: (actor) => call(actor, "GET", "models", list(MODEL)),
    deployments: (actor) => call(actor, "GET", "deployments", list(DEPLOYMENT)),
    proposals: (actor) => call(actor, "GET", "proposals", list(PROPOSAL)),
    aggregates: (actor) => call(actor, "GET", "aggregates", list(AGGREGATE)),
    register: (actor, r) => call(actor, "POST", "register", DEPLOYMENT,
      { name: r.name, artifact_digest: r.artifactDigest, schema_version: r.schemaVersion, runtime: r.runtime }),
    smoke: (actor, id) => call(actor, "POST", `deployments/${encodeURIComponent(id)}/smoke`, DEPLOYMENT),
    propose: (actor, kind, id) => call(actor, "POST", "proposals", PROPOSAL, { kind, deployment_revision_id: id }),
  };
}
