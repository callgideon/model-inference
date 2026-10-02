// AP-00 00c: the thin fetch transport under the generated clients (R270, R271).
//
// A server-side web adapter calls infrx-api as the signed-in user: it forwards the session
// (a Bearer token and/or the cookie header) and nothing else - no service key, no DSN, no
// API key minted here. Every call is `no-store`, carries an `X-Request-Id`, and returns a
// typed Result: the R270 error envelope, the Lab's legacy `{refusal}` and the OpenAI-style
// `/v1` error each map to their own `ApiError` kind, and anything unreadable fails closed as
// `unavailable` (never an empty success).

type Method = "get" | "post" | "put" | "delete" | "patch";

/** The operation `M` of path item `I` in an openapi-typescript `paths` type. */
type Op<I, M extends Method> = I extends { [K in M]: infer O } ? O : never;
type JsonOf<R> = R extends { content: { "application/json": infer B } } ? B : unknown;
/** The body of the operation's 2xx answer (`unknown` for a legacy route with no schema). */
export type Success<O> = O extends { responses: infer R }
  ? JsonOf<R[Extract<keyof R, 200 | 201 | 202 | 204>]>
  : unknown;
export type BodyOf<O> = O extends { requestBody: { content: { "application/json": infer B } } } ? B : never;
type QueryOf<O> = O extends { parameters: { query?: infer Q } } ? Q : never;
type MethodsOf<I> = { [M in Method]: I extends { [K in M]: infer O } ? ([O] extends [never] ? never : M) : never }[Method];

export type FieldError = { field: string; code: string; message: string };
export type ApiError =
  | { kind: "error"; status: number; code: string; message: string; requestId: string; retryable: boolean;
      fieldErrors: FieldError[]; operationId: string | null; resourceId: string | null }
  | { kind: "refusal"; status: number; reason: string }
  | { kind: "openai"; status: number; code: string | null; message: string }
  | { kind: "unavailable"; status: number | null; reason: "network" | "malformed" };
export type Result<T> =
  | { ok: true; status: number; data: T; requestId: string; location: string | null }
  | { ok: false; error: ApiError; requestId: string };

/** What the web server forwards: the user's session token and/or its cookie header. */
export type Session = { token?: string | null; cookie?: string | null };

export type ClientOptions = {
  baseUrl: string;
  session?: () => Session | Promise<Session>;
  fetch?: typeof fetch;
  requestId?: () => string;
  timeoutMs?: number;
};

export type CallInit<O> = {
  params?: Record<string, string>;
  query?: QueryOf<O> | Record<string, string | number | undefined>;
  body?: BodyOf<O>;
  idempotencyKey?: string;
};

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

/** A failed answer's body as one of the three wire formats, or `malformed`. */
export function errorOf(status: number, body: unknown): ApiError {
  if (isObject(body) && isObject(body.error)) {
    const e = body.error;
    if (typeof e.code === "string" && typeof e.message === "string" && typeof e.request_id === "string"
        && typeof e.retryable === "boolean") {
      const fields = Array.isArray(e.field_errors) ? e.field_errors : [];
      return { kind: "error", status, code: e.code, message: e.message, requestId: e.request_id,
        retryable: e.retryable, operationId: str(e.operation_id), resourceId: str(e.resource_id),
        fieldErrors: fields.filter(isObject).map((f) => ({ field: String(f.field), code: String(f.code), message: String(f.message) })) };
    }
    if (typeof e.message === "string") return { kind: "openai", status, code: str(e.code), message: e.message };
  }
  if (isObject(body) && typeof body.refusal === "string") return { kind: "refusal", status, reason: body.refusal };
  return { kind: "unavailable", status, reason: "malformed" };
}

function urlOf(base: string, path: string, init: { params?: Record<string, string>; query?: unknown }): string {
  const filled = path.replace(/\{([^}]+)\}/g, (_, name: string) => {
    const value = init.params?.[name];
    if (value === undefined) throw new Error(`missing path parameter ${name}`);
    return encodeURIComponent(value);
  });
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries((init.query ?? {}) as Record<string, unknown>)) {
    if (v !== undefined && v !== null) query.set(k, String(v));
  }
  const qs = query.toString();
  return `${base.replace(/\/+$/, "")}${filled}${qs ? `?${qs}` : ""}`;
}

export type Client<P> = {
  call<Pth extends keyof P & string, M extends MethodsOf<P[Pth]>>(
    method: M, path: Pth, init?: CallInit<Op<P[Pth], M>>,
  ): Promise<Result<Success<Op<P[Pth], M>>>>;
};

export function createClient<P>(options: ClientOptions): Client<P> {
  const send = options.fetch ?? fetch;
  return {
    async call(method, path, init = {}) {
      const requestId = options.requestId?.() ?? crypto.randomUUID();
      const session = (await options.session?.()) ?? {};
      const headers: Record<string, string> = { accept: "application/json", "x-request-id": requestId };
      if (session.token) headers.authorization = `Bearer ${session.token}`;
      if (session.cookie) headers.cookie = session.cookie;
      if (init.idempotencyKey) headers["idempotency-key"] = init.idempotencyKey;
      if (init.body !== undefined) headers["content-type"] = "application/json";
      let answer: Response;
      try {
        answer = await send(urlOf(options.baseUrl, path, init), {
          method: method.toUpperCase(), headers, cache: "no-store", redirect: "manual",
          body: init.body === undefined ? undefined : JSON.stringify(init.body),
          signal: AbortSignal.timeout(options.timeoutMs ?? 10_000),
        });
      } catch {
        return { ok: false, requestId, error: { kind: "unavailable", status: null, reason: "network" } };
      }
      let body: unknown = null;
      const text = await answer.text().catch(() => null);
      if (text === null) return { ok: false, requestId, error: { kind: "unavailable", status: answer.status, reason: "network" } };
      if (text !== "") {
        try {
          body = JSON.parse(text);
        } catch {
          return { ok: false, requestId, error: { kind: "unavailable", status: answer.status, reason: "malformed" } };
        }
      }
      if (answer.status >= 200 && answer.status < 300) {
        return { ok: true, status: answer.status, data: body as never, requestId, location: answer.headers.get("location") };
      }
      return { ok: false, requestId, error: errorOf(answer.status, body) };
    },
  };
}
