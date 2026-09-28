// WR-LAB-API-5: the Lab's adapter over lab-api's provider trace read (R176, gateway/routes/lab_traces.py):
//   GET /lab/v1/traces?provider_org_id=&limit=&cursor=   -> { data: [item], next_cursor }
//   GET /lab/v1/traces/{request_id}?provider_org_id=      -> item
// The credential is the signed-in user's own session token (lab_auth.py) and the provider is always the
// session workspace; the route decides every tenancy question. Only the route's named fields are kept.
import type { Page } from "@infrx/shared/console/types.ts";
import type { Actor, Result, TraceDetail, TraceReadPort, TraceRefusal } from "../../../components/traces/detail/port.ts";

/** The route's default page (1..200); the list asks for exactly this many. */
export const LIST_LIMIT = 50;
export type TracePage = Page<TraceDetail>;
export interface TraceService extends TraceReadPort {
  list(actor: Actor, cursor: string | null): Promise<Result<TracePage, TraceRefusal>>;
}

const STATUS: Record<number, TraceRefusal> = { 401: "denied", 403: "denied", 404: "not_found" };
const refuse = (reason: TraceRefusal) => ({ ok: false, reason }) as const;
const UNAVAILABLE = refuse("unavailable");

/** Field -> the JSON types it may hold (`typeof`, or "null"). Anything unnamed is dropped. */
type Spec = Record<string, readonly string[]>;
const S = ["string"], SN = ["string", "null"];
const METADATA: Spec = {
  request_id: S, started_at: S, completed_at: SN, mode: S, loss_reason: S, serving_version_id: S,
  model_revision: S, rate_card_version: SN, policy_version: SN, model_id: S,
};
const GRANTED: Spec = { grantor_org_id: S, grant_ref: S, content_complete: ["boolean"], content_bytes: ["number"], content_available: ["boolean"] };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

function pick(raw: Record<string, unknown>, spec: Spec): Record<string, unknown> | null {
  const out: Record<string, unknown> = {};
  for (const [name, kinds] of Object.entries(spec)) {
    const value = raw[name];
    if (!kinds.includes(value === null ? "null" : typeof value)) return null;
    out[name] = value;
  }
  return out;
}

/** One item. A list row reads no content, so a granted row there may lack `grant_ref` (it is never used). */
function item(raw: unknown, listRow: boolean): TraceDetail | null {
  if (!isObj(raw)) return null;
  const meta = pick(raw, METADATA);
  if (meta === null) return null;
  if (raw.access === "metadata") return { ...meta, access: "metadata" } as TraceDetail;
  if (raw.access !== "content") return null;
  const granted = pick(listRow && raw.grant_ref === undefined ? { ...raw, grant_ref: "" } : raw, GRANTED);
  return granted === null ? null : ({ ...meta, ...granted, access: "content" } as TraceDetail);
}

export type HttpOptions = { baseUrl: string; token: () => Promise<string | null>; fetch?: typeof fetch };

export function httpTraces({ baseUrl, token, fetch: send = fetch }: HttpOptions): TraceService {
  const root = `${baseUrl.replace(/\/+$/, "")}/lab/v1/traces`;
  async function get(path: string, query: Record<string, string>): Promise<Result<unknown, TraceRefusal>> {
    const bearer = await token().catch(() => null);
    if (!bearer) return UNAVAILABLE;
    let response: Response;
    try {
      response = await send(`${root}${path}?${new URLSearchParams(query)}`, { headers: { authorization: `Bearer ${bearer}` }, cache: "no-store" });
    } catch {
      return UNAVAILABLE;
    }
    if (!response.ok) return refuse(STATUS[response.status] ?? "unavailable");
    try {
      return { ok: true, value: await response.json() };
    } catch {
      return UNAVAILABLE;
    }
  }
  return {
    async list(actor, cursor) {
      const answer = await get("", { provider_org_id: actor.providerId, limit: String(LIST_LIMIT), ...(cursor === null ? {} : { cursor }) });
      if (!answer.ok) return answer;
      const body = answer.value;
      if (!isObj(body) || !Array.isArray(body.data) || !(body.next_cursor === null || typeof body.next_cursor === "string")) return UNAVAILABLE;
      const items = body.data.map((raw) => item(raw, true));
      if (items.some((x) => x === null)) return UNAVAILABLE;
      return { ok: true, value: { items: items as TraceDetail[], next_cursor: body.next_cursor } };
    },
    async detail(actor, requestId) {
      const answer = await get(`/${encodeURIComponent(requestId)}`, { provider_org_id: actor.providerId });
      if (!answer.ok) return answer;
      const one = item(answer.value, false);
      return one === null ? UNAVAILABLE : { ok: true, value: one };
    },
  };
}

/** Not configured: every read is unavailable (fails closed), and nothing is sent anywhere. */
export function offlineTraces(): TraceService {
  return { list: async () => UNAVAILABLE, detail: async () => UNAVAILABLE };
}
