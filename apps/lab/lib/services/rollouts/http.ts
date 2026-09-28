// WR-R4-1: the releases port over infrx-api's `/lab/v1/releases` and `/lab/v1/optimizations`
// (LAB_RELEASES), as the signed-in user: the credential is the session's own access token and the
// provider is the actor's, sent as `provider_org_id`; the route re-derives both. The route speaks
// snake_case (D9/R1/R2/R3's records), the port camelCase: every key is renamed, no value is touched.
// Lists come as `{data}`; every refusal is the route's status mapped to the port's reason.
import type { Actor, Refusal, ReleasesPort, Result } from "./port.ts";

const REASONS: Record<number, Refusal> = { 401: "denied", 403: "denied", 404: "not_found", 409: "conflict", 422: "invalid" };
export type HttpOptions = { baseUrl: string; token: string; fetch?: typeof fetch };

/** snake_case keys to camelCase, deep; these records carry no data-keyed maps. */
export const camel = (value: unknown): unknown =>
  Array.isArray(value) ? value.map(camel)
    : value !== null && typeof value === "object"
      ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase()), camel(v)]))
      : value;

export function httpReleases({ baseUrl, token, fetch: send = fetch }: HttpOptions): ReleasesPort {
  const root = `${baseUrl.replace(/\/+$/, "")}/lab/v1`;
  async function call<T>(actor: Actor, path: string, body?: unknown): Promise<Result<T>> {
    const url = `${root}/${path}?provider_org_id=${encodeURIComponent(actor.providerId)}`;
    const headers: Record<string, string> = { authorization: `Bearer ${token}` };
    if (body !== undefined) headers["content-type"] = "application/json";
    try {
      const response = await send(url, {
        method: body === undefined ? "GET" : "POST", headers, cache: "no-store",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };
      const payload = await response.json();
      return { ok: true, value: camel(body === undefined ? payload.data : payload) as T }; // a read is {data}
    } catch {
      return { ok: false, reason: "unavailable" }; // transport or an unreadable answer
    }
  }
  return {
    releases: (actor) => call(actor, "releases"),
    variants: (actor) => call(actor, "optimizations"),
    propose: (actor, kind, policyRef, fence) => call(actor, "releases/proposals", { kind, policy_ref: policyRef, fence }),
  };
}
