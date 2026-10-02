// AP-09 09a/09e: the client fake - an in-memory infrx-api for the console's routes, answered over
// the generated client's own transport (a `fetch`), so the adapters under test are the production
// ones. Its documents are typed by the generated OpenAPI schemas (`make console-typecheck` fails a
// fake that drifts from the contract). Used by the tests and by the INFRX_CONSOLE_PREVIEW
// development preview (never a production build: usage/fake-console-context.ts).
//
// The default world is a wallet mid-flight - settled, pending, unknown, free, absorbed, cancelled and
// legacy USD requests - whose figures are the API's own (stated, not derived here).
import type { components } from "@infrx/api-client/consumer";
import { consumerApi, type ConsumerApi } from "./api/index.ts";

type S = components["schemas"];
export type FakeWorld = {
  me: S["Me"] | null;
  capabilities: S["ConsoleCapabilities"];
  credits: S["Credits"];
  ledger: S["LedgerEntry"][];
  legacy: S["LegacyStatement"];
  requests: S["RequestSummary"][];
  results: Record<string, string>;
  keys: S["KeySummary"][];
  members: S["infrx__console__reads__Member"][];
  /** request_id -> the key it was sent with (a summary names no key; the filter is the API's). */
  keyOf: Record<string, string>;
  accounts: S["OperatorAccount"][];
  drift: S["WalletDrift"][];
  unknownUsage: S["UnknownUsage"][];
  audit: S["AuditEntry"][];
};

export const FAKE_ORIGIN = "http://infrx-api.fake";
export const FAKE_CLOCK = "2026-09-20T12:00:00.000000Z";
export const FAKE_USER = "a2000000-0000-4000-8000-000000000001";
export const FAKE_ORG = "0a000000-0000-4000-8000-0000000000aa";
export const FAKE_KEY = "c7000000-0000-4000-8000-0000000000f1";
const MODEL = "nemostation/marlin-2b";
const REVISION = "nemostation/marlin-2b@2026-09-01";

const C = (amount: string): S["Money"] => ({ amount, unit: "CREDIT" });

function request(n: number, created_at: string, over: Partial<S["RequestSummary"]>): S["RequestSummary"] {
  return {
    request_id: `b1000000-0000-4000-8000-00000000000${n}`,
    created_at,
    requested_model: MODEL,
    model_revision: REVISION,
    execution_mode: "async",
    state: "succeeded",
    outcome_cause: "completed",
    accounting_regime: "credit",
    settlement_state: "settled",
    usage_certainty: "authoritative",
    prompt_tokens: 1200,
    completion_tokens: 340,
    hold: C("5.00000000"),
    hold_state: "settled",
    charged: null,
    result_access: "no_result",
    result_expires_at: null,
    ...over,
  };
}

export function defaultWorld(): FakeWorld {
  const requests = [
    request(1, "2026-09-20T11:00:00.000000Z", { charged: C("1.23456789"), result_access: "available", result_expires_at: "2026-09-20T12:10:00.000000Z" }),
    // A success with no persisted expiry: the API cannot serve its content (`unavailable`).
    request(2, "2026-09-20T11:00:00.000000Z", { charged: C("0.98765432"), execution_mode: "sync", result_access: "unavailable" }),
    request(3, "2026-09-20T11:30:00.000000Z", { state: "running", outcome_cause: null, settlement_state: null, usage_certainty: null, prompt_tokens: null, completion_tokens: null, hold_state: "held", result_access: "pending" }),
    request(4, "2026-09-20T11:20:00.000000Z", { state: "failed", outcome_cause: "engine_incomplete", settlement_state: "held_unknown", usage_certainty: "unknown", prompt_tokens: null, completion_tokens: null, hold_state: "unknown", result_access: "held_unknown" }),
    request(5, "2026-09-20T11:10:00.000000Z", { state: "failed", outcome_cause: "invalid_media", settlement_state: "released_free", usage_certainty: null, prompt_tokens: null, completion_tokens: null, hold_state: "released" }),
    request(6, "2026-09-20T11:05:00.000000Z", { state: "failed", outcome_cause: "sync_deadline", settlement_state: "released_platform_absorbed", usage_certainty: null, prompt_tokens: null, completion_tokens: null, hold_state: "released", execution_mode: "sync" }),
    request(7, "2026-09-20T10:30:00.000000Z", { state: "cancelled", outcome_cause: "client_cancelled", charged: C("0.10000000"), prompt_tokens: 1200, completion_tokens: 20, execution_mode: "stream" }),
    request(8, "2026-09-19T12:00:00.000000Z", { accounting_regime: "legacy_usd", charged: { amount: "0.00019660", unit: "USD" }, hold: { amount: "0.00500000", unit: "USD" }, model_revision: MODEL, result_access: "unavailable" }),
  ];
  const debit = (n: number, id: string, created_at: string, amount: string): S["LedgerEntry"] => ({
    entry_id: id, created_at, kind: "inference_debit", amount: C(amount), request_id: `b1000000-0000-4000-8000-00000000000${n}`, reason: "inference",
  });
  return {
    me: {
      actor: { audience: "session", user_id: FAKE_USER, org_id: FAKE_ORG, operator: false },
      state: "ready",
      suspended: false,
      signup_grant: { state: "granted", amount: C("10000.00000000"), granted_at: "2026-09-20T09:00:00Z" },
    },
    capabilities: {
      actions: { create_key: true, revoke_key: true, claim_signup_grant: false, operator_console: false },
      features: {
        feedback: { state: "configured", reason: null, verified_at: FAKE_CLOCK },
        trace_export: { state: "disabled", reason: "switch_off", verified_at: FAKE_CLOCK },
        dedicated_endpoints: { state: "disabled", reason: "not_offered", verified_at: null },
      },
    },
    // Stated by the API: ledger 10000 - 5 - 2.32222221; reserved = the two open holds; spent = the debits.
    credits: {
      wallet_id: "a1000000-0000-4000-8000-00000000000a",
      ledger_total: C("9992.67777779"),
      reserved_total: C("10.00000000"),
      available: C("9982.67777779"),
      spent: C("2.32222221"),
      signup_granted_at: "2026-09-20T09:00:00.000000Z",
    },
    ledger: [
      debit(1, "e1000000-0000-4000-8000-0000000000d0", "2026-09-20T11:00:05.000000Z", "-1.23456789"),
      debit(2, "e1000000-0000-4000-8000-0000000000d1", "2026-09-20T11:00:05.000000Z", "-0.98765432"),
      debit(7, "e1000000-0000-4000-8000-0000000000d2", "2026-09-20T10:30:05.000000Z", "-0.10000000"),
      { entry_id: "e1000000-0000-4000-8000-0000000000a2", created_at: "2026-09-20T10:00:00.000000Z", kind: "operator_adjustment", amount: C("-5.00000000"), request_id: null, reason: "Correction of a duplicated test grant" },
      { entry_id: "e1000000-0000-4000-8000-0000000000a1", created_at: "2026-09-20T09:00:00.000000Z", kind: "signup_grant", amount: C("10000.00000000"), request_id: null, reason: "" },
    ],
    legacy: { balance: { amount: "4.99980340", unit: "USD" }, entry_count: 2, rollout_hold: true, as_of: FAKE_CLOCK },
    requests,
    results: { "b1000000-0000-4000-8000-000000000001": "Demo result for request b1000000-0000-4000-8000-000000000001. This is an example, not your output." },
    keys: [{ id: FAKE_KEY, name: "preview", prefix: "sk-infrx-previ", created_at: "2026-09-20T08:00:00.000000Z", last_used_at: null, revoked_at: null }],
    members: [{ user_id: FAKE_USER, email: "preview@example.com", role: "owner", joined_at: "2026-09-20T08:00:00.000000Z" }],
    keyOf: Object.fromEntries(requests.map((r) => [r.request_id, FAKE_KEY])),
    accounts: [],
    drift: [],
    unknownUsage: [],
    audit: [],
  };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const refuse = (status: number, code: string) =>
  json({ error: { code, message: code.replaceAll("_", " "), request_id: "fake", retryable: status === 503, field_errors: [] } }, status);

/** `[created_at desc, id]` order, then an offset cursor: a fake's cursor is as opaque as the API's. */
function page<T>(items: T[], url: URL): Response {
  const limit = Number(url.searchParams.get("limit") ?? "25");
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) return refuse(422, "invalid_request");
  const cursor = url.searchParams.get("cursor");
  const start = cursor === null ? 0 : Number(cursor);
  if (!Number.isInteger(start) || start < 0) return refuse(400, "invalid_cursor");
  const shown = items.slice(start, start + limit);
  return json({ data: shown, next_cursor: start + limit < items.length ? String(start + limit) : null });
}

/** Newest first; ties by id, ascending (requests) or descending (ledger, keys), as the API pages them. */
const newest = <T extends { created_at: string }>(id: (t: T) => string, idDesc = false) => (a: T, b: T) =>
  a.created_at === b.created_at ? ((id(a) < id(b)) !== idDesc ? -1 : 1) : a.created_at < b.created_at ? 1 : -1;

/** A `fetch` that answers the console's routes from `world`. Unknown routes are a 404 envelope. */
export function fakeConsoleFetch(world: FakeWorld = defaultWorld()): typeof fetch {
  return (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname;
    const route = `${method} ${path}`;
    if (world.me === null) return refuse(401, "invalid_api_key");
    const one = /^\/console\/v1\/requests\/([^/]+)(\/result)?$/.exec(path);
    if (method === "GET" && one !== null) {
      const found = world.requests.find((r) => r.request_id === one[1]);
      if (found === undefined) return refuse(404, "not_found");
      if (one[2] === undefined) return json(found);
      if (found.result_access !== "available") return refuse(found.result_access === "pending" ? 409 : 410, found.result_access === "pending" ? "result_pending" : "result_expired");
      return json({ request_id: found.request_id, text: world.results[found.request_id] ?? "", expires_at: found.result_expires_at });
    }
    switch (route) {
      case "GET /console/v1/me":
        return json(world.me);
      case "GET /console/v1/capabilities":
        return json(world.capabilities);
      case "GET /console/v1/credits":
        return json(world.credits);
      case "GET /console/v1/legacy-statement":
        return json(world.legacy);
      case "GET /console/v1/credit-ledger":
        return page([...world.ledger].sort(newest((e) => e.entry_id, true)), url);
      case "GET /console/v1/requests": {
        const q = (name: string) => url.searchParams.get(name);
        const kept = world.requests.filter((r) =>
          (!q("model") || q("model") === r.requested_model || q("model") === r.model_revision) &&
          (!q("key_id") || world.keyOf[r.request_id] === q("key_id")) &&
          (!q("from") || r.created_at >= (q("from") as string)) &&
          (!q("to") || r.created_at < (q("to") as string)));
        return page(kept.sort(newest((r) => r.request_id)), url);
      }
      case "GET /console/v1/keys":
        return page([...world.keys].sort(newest((k) => k.id, true)), url);
      case "GET /console/v1/account/members":
        return page(world.members, url);
      case "GET /operator/v1/accounts":
        return world.me.actor.operator ? page(world.accounts, url) : refuse(403, "forbidden");
      case "GET /operator/v1/wallet-drift":
        return world.me.actor.operator ? page(world.drift, url) : refuse(403, "forbidden");
      case "GET /operator/v1/unknown-usage":
        return world.me.actor.operator ? page(world.unknownUsage, url) : refuse(403, "forbidden");
      case "GET /operator/v1/audit":
        return world.me.actor.operator ? page(world.audit, url) : refuse(403, "forbidden");
      default:
        return refuse(404, "not_found");
    }
  }) as typeof fetch;
}

export function fakeConsoleApi(world: FakeWorld = defaultWorld()): ConsumerApi {
  return consumerApi({ baseUrl: FAKE_ORIGIN, fetch: fakeConsoleFetch(world), requestId: () => "fake-request" });
}

export type Sent = { method: string; path: string; body: unknown; auth: string | null; idempotencyKey: string | null };

/** The generated client over a recording `fetch`: every request is kept, `reply` answers it (tests). */
export function recordingApi(reply: (sent: Sent) => Response | Promise<Response>): { api: ConsumerApi; sent: Sent[] } {
  const sent: Sent[] = [];
  const api = consumerApi({
    baseUrl: FAKE_ORIGIN,
    requestId: () => "fake-request",
    fetch: (async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input));
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const one: Sent = {
        method: String(init?.method ?? "GET"),
        path: url.pathname + url.search,
        body: init?.body ? JSON.parse(String(init.body)) : null,
        auth: headers.authorization ?? null,
        idempotencyKey: headers["idempotency-key"] ?? null,
      };
      sent.push(one);
      return reply(one);
    }) as typeof fetch,
  });
  return { api, sent };
}

/** A JSON answer, and the R270 error envelope (tests and the fake). */
export const answer = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
export const envelope = (code: string, message = "SECRET server text") => ({ error: { code, message, request_id: "fake", retryable: false, field_errors: [] } });
