// WR-B4-1: the evaluation port over infrx-api's `/lab/v1/evaluations` (LAB_EVALS), as the signed-in
// user: the credential is the session's own access token and the provider is the actor's, sent as
// `provider_org_id`; the route re-derives both. Records are the backends' JSON verbatim (snake_case),
// lists come as `{data}`, and every refusal is the route's status mapped to the port's reason.
import type { Actor, EvaluationPort, Refusal, Result } from "./port.ts";

const REASONS: Record<number, Refusal> = { 401: "denied", 403: "denied", 404: "not_found", 409: "conflict", 422: "invalid" };
export type HttpOptions = { baseUrl: string; token: string; fetch?: typeof fetch };

export function httpEvaluation({ baseUrl, token, fetch: send = fetch }: HttpOptions): EvaluationPort {
  const root = `${baseUrl.replace(/\/+$/, "")}/lab/v1/evaluations`;
  async function call<T>(actor: Actor, method: "GET" | "POST", path: string, body?: unknown): Promise<Result<T>> {
    const url = `${root}/${path}?provider_org_id=${encodeURIComponent(actor.providerId)}`;
    const headers: Record<string, string> = { authorization: `Bearer ${token}` };
    if (body !== undefined) headers["content-type"] = "application/json";
    try {
      const response = await send(url, {
        method, headers, cache: "no-store",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };
      const payload = await response.json();
      return { ok: true, value: (method === "GET" ? payload.data : payload) as T }; // a read is a {data} list
    } catch {
      return { ok: false, reason: "unavailable" }; // transport or an unreadable answer
    }
  }
  return {
    catalog: (actor) => call(actor, "GET", "catalog"),
    runs: (actor) => call(actor, "GET", "runs"),
    experiments: (actor) => call(actor, "GET", "experiments"),
    subscriptions: (actor) => call(actor, "GET", "subscriptions"),
    launch: (actor, launch) => call(actor, "POST", "experiments", launch),
    cancel: (actor, runId) => call(actor, "POST", `runs/${encodeURIComponent(runId)}/cancel`),
    subscribe: (actor, request) => call(actor, "POST", "subscriptions", request),
  };
}
