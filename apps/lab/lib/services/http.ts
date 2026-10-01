// LAB-04: the one HTTP transport of the Lab's lab-api adapters (control, evaluation, pipelines, releases).
// It replaces the four copied call loops of control/evaluation/pipelines/rollouts http.ts, the two camel()
// copies (rollouts, pipelines) and evaluation/shape.ts's row checks, so no family imports another.
// As the signed-in user: the credential is the session's own access token (none: nothing is sent) and the
// provider is the actor's, sent as `provider_org_id`; the route re-derives both. A refusal is the route's
// status mapped to the port's reason; an answer holding one record the pages cannot read is unavailable
// (fails closed). datasets/port.ts and traces/port.ts keep their own (throwing-parser) transports.

/** A row check: a record the page cannot read (a missing field, a value of another type, a state it does
 *  not know) fails the whole answer closed instead of reaching a page. Unnamed fields pass through. */
export type Check = (value: unknown) => boolean;

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
export const str: Check = (v) => typeof v === "string";
export const num: Check = (v) => typeof v === "number" && Number.isFinite(v);
export const bool: Check = (v) => typeof v === "boolean";
export const nul = (check: Check): Check => (v) => v === null || check(v);
export const opt = (check: Check): Check => (v) => v === undefined || check(v);
export const oneOf = (...values: readonly unknown[]): Check => (v) => values.includes(v);
export const list = (check: Check): Check => (v) => Array.isArray(v) && v.every(check);
export const map = (check: Check): Check => (v) => isObj(v) && Object.values(v).every(check);
export const obj = (spec: Record<string, Check>): Check => (v) => isObj(v) && Object.entries(spec).every(([k, check]) => check(v[k]));

const rename = (to: (key: string) => string) => {
  const deep = (value: unknown): unknown =>
    Array.isArray(value) ? value.map(deep)
      : value !== null && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([k, v]) => [to(k), deep(v)])) : value;
  return deep;
};
/** Deep key renames (the routes speak snake_case, the ports camelCase); these records carry no data-keyed maps. */
export const camel = rename((k) => k.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase()));
export const snake = rename((k) => k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`));

/** The routes' refusals every family maps; a family adds its own (pipelines: 410 gone). */
export const REASONS = { 401: "denied", 403: "denied", 404: "not_found", 409: "conflict", 422: "invalid" } as const;
type Reason = (typeof REASONS)[keyof typeof REASONS];

export type HttpOptions = { baseUrl: string; token: () => Promise<string | null>; fetch?: typeof fetch };
/** The JSON answer to the value the row check reads. */
export type Answer = (payload: { data?: unknown }) => unknown;
type Outcome<T, R> = { ok: true; value: T } | { ok: false; reason: R | "unavailable" };

/** `route` is the family's root under the base URL; `rename` maps the route's keys to the port's (default: none). */
export function labClient<R extends string = Reason>(
  { baseUrl, token, fetch: send = fetch }: HttpOptions, route: string,
  { reasons = REASONS as unknown as Record<number, R>, rename: keys = (v: unknown) => v }: { reasons?: Record<number, R>; rename?: (v: unknown) => unknown } = {},
) {
  const root = `${baseUrl.replace(/\/+$/, "")}${route}`;
  async function call<T>(actor: { providerId: string }, method: "GET" | "POST", path: string, read: Answer, readable: Check, body?: unknown, query = ""): Promise<Outcome<T, R>> {
    const bearer = await token().catch(() => null);
    if (!bearer) return { ok: false, reason: "unavailable" }; // no session: nothing is sent
    const url = `${root}/${path}?provider_org_id=${encodeURIComponent(actor.providerId)}${query}`;
    const headers: Record<string, string> = { authorization: `Bearer ${bearer}` };
    if (body !== undefined) headers["content-type"] = "application/json";
    try {
      const response = await send(url, { method, headers, cache: "no-store", body: body === undefined ? undefined : JSON.stringify(body) });
      if (!response.ok) return { ok: false, reason: reasons[response.status] ?? "unavailable" };
      const value = read(await response.json());
      return readable(value) ? { ok: true, value: value as T } : { ok: false, reason: "unavailable" }; // an unreadable record
    } catch {
      return { ok: false, reason: "unavailable" }; // transport or an unparseable answer
    }
  }
  return {
    /** A read: the answer is `{data}`. */
    get: <T>(actor: { providerId: string }, path: string, readable: Check, read: Answer = (p) => keys(p.data), query = "") =>
      call<T>(actor, "GET", path, read, readable, undefined, query),
    /** A write: the answer is the record itself. */
    post: <T>(actor: { providerId: string }, path: string, readable: Check, body?: unknown, read: Answer = keys) =>
      call<T>(actor, "POST", path, read, readable, body),
  };
}
