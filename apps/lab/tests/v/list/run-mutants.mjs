#!/usr/bin/env node
// V1M's mutant runner (R32; LANE-RULES addendum) over tests/v/list, on the shared Lab harness. Moved from
// the App's tests/v runner; stack.test.ts (the real route) is outside it, like V2's feedback-postgrest.
// Usage: node tests/v/list/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["adapter", "wiring", "query", "view", "page"].map((f) => `tests/v/list/${f}.test.ts`);
const PORT = "lib/services/traces/port.ts";
const SERVER = "lib/services/traces/server.ts";
const SESSION = "lib/auth/session.ts"; // LAB-05: the one read-only session client
const WIRE = "components/traces/detail/port.ts";
const QUERY = "components/traces/list/query.ts";
const VIEW = "components/traces/list/view-model.ts";
const TABLE = "components/traces/list/trace-table.tsx";
const PAGE = "app/(provider)/requests/page.tsx";
const ERROR = "app/(provider)/requests/error.tsx";

const C = {
  a01: "V1M-A01 reads as the session's workspace with the user's own token: the list and one request",
  a02: "V1M-A02 refusals map to denied, not_found or unavailable, and no token sends nothing",
  a03: "V1M-A03 only the route's named fields are kept: a metadata row never carries an organization, key, size or content",
  a04: "V1M-A04 an answer that is not the route's shape is unavailable, never a partial record",
  w01: "V1M-W01 the detail page's trace port is the Lab adapter, reading with the session's access token",
  w02: "V1M-W02 unset LAB_TRACES_API_URL or Supabase config is off: every read unavailable, nothing sent",
  q01: "V1M-Q01 the route's cursor passes through exactly; every other name is ignored and reported, sorted",
  q02: "V1M-Q02 a repeated, oversized or non-base64url cursor is refused with its reason, never sent",
  q03: "V1M-Q03 page links are /requests plus the cursor, and the first page carries none",
  l01: "V1M-L01 each row links to its request page and shows named metadata only: no organization, size or content",
  l02: "V1M-L02 content reads honestly: shared, metadata only, not captured, lost with its reason, expired",
  l03: "V1M-L03 an empty workspace says capture-off requests leave no record; an empty page with more to come says so",
  l04: "V1M-L04 the next link is the server's cursor, and the way back to the first page shows only on a later page",
  l05: "V1M-L05 a refusal is fixed copy: a viewer's role, a lost workspace, an unreachable service; never rows",
  p01: "V1M-P01 the list reads as the session's workspace through the Lab trace service",
  p02: "V1M-P02 rows are native links to the request page; states and refusals are text; no App kit, no scripts on rows",
};

const MUTANTS = [
  // the adapter (WR-LAB-API-5)
  m("V1M-X01", "a 401 is not denied", PORT, '401: "denied", ', "", [C.a02]),
  m("V1M-X02", "a 403 reads as not found", PORT, '403: "denied"', '403: "not_found"', [C.a02]),
  m("V1M-X03", "a 404 reads as denied", PORT, '404: "not_found"', '404: "denied"', [C.a02]),
  m("V1M-X04", "any other status reads as not found", PORT, 'STATUS[response.status] ?? "unavailable"', 'STATUS[response.status] ?? "not_found"', [C.a02]),
  m("V1M-X05", "a tokenless read is still sent", PORT, "    if (!bearer) return UNAVAILABLE;\n", "", [C.a02]),
  m("V1M-X06", "a failed token read throws", PORT, "await token().catch(() => null)", "await token()", [C.a02]),
  m("V1M-X07", "an unreachable service reads as an empty page", PORT, "    } catch {\n      return UNAVAILABLE;\n    }\n    if (!response.ok)", '    } catch {\n      return { ok: true, value: { data: [], next_cursor: null } };\n    }\n    if (!response.ok)', [C.a02]),
  m("V1M-X08", "an unreadable body throws", PORT, "    try {\n      return { ok: true, value: await response.json() };\n    } catch {\n      return UNAVAILABLE;\n    }", "    return { ok: true, value: await response.json() };", [C.a04]),
  m("V1M-X09", "a read may be cached", PORT, 'cache: "no-store"', 'cache: "default"', [C.a01]),
  m("V1M-X10", "the token is not a bearer credential", PORT, "authorization: `Bearer ${bearer}`", "authorization: `${bearer}`", [C.a01]),
  m("V1M-X11", "the list does not name the session's workspace", PORT, "provider_org_id: actor.providerId, limit", 'provider_org_id: "", limit', [C.a01]),
  m("V1M-X12", "the detail does not name the session's workspace", PORT, "{ provider_org_id: actor.providerId })", "{})", [C.a01]),
  m("V1M-X13", "the list asks for the route's maximum", PORT, "limit: String(LIST_LIMIT)", 'limit: "200"', [C.a01]),
  m("V1M-X14", "the cursor is dropped", PORT, "...(cursor === null ? {} : { cursor })", "...{}", [C.a01]),
  m("V1M-X15", "the request id is not encoded into the path", PORT, "`/${encodeURIComponent(requestId)}`", "`/${requestId}`", [C.a01]),
  m("V1M-X16", "the page size is not the route's default", PORT, "export const LIST_LIMIT = 50;", "export const LIST_LIMIT = 200;", [C.a01]),
  m("V1M-X17", "a trailing slash doubles into the path", PORT, 'baseUrl.replace(/\\/+$/, "")', "baseUrl", [C.a01]),
  m("V1M-X18", "a metadata row keeps unnamed fields", PORT, 'return { ...meta, access: "metadata" } as TraceDetail;', 'return { ...raw, ...meta, access: "metadata" } as TraceDetail;', [C.a03]),
  m("V1M-X19", "a granted row keeps unnamed fields", PORT, '({ ...meta, ...granted, access: "content" }', '({ ...raw, ...meta, ...granted, access: "content" }', [C.a03]),
  m("V1M-X20", "field types are not checked", PORT, '    if (!kinds.includes(value === null ? "null" : typeof value)) return null;\n', "", [C.a04]),
  m("V1M-X21", "null passes for a required string", PORT, 'const S = ["string"], SN', 'const S = ["string", "null"], SN', [C.a04]),
  m("V1M-X22", "an unknown access level reads as content", PORT, '  if (raw.access !== "content") return null;\n', "", [C.a04]),
  m("V1M-X23", "a granted detail need not name its grant", PORT, "listRow && raw.grant_ref === undefined ?", "raw.grant_ref === undefined ?", [C.a04]),
  m("V1M-X24", "a granted list row without its grant is refused", PORT, 'listRow && raw.grant_ref === undefined ? { ...raw, grant_ref: "" } : raw', "raw", [C.a04]),
  m("V1M-X25", "a non-object body is read", PORT, "if (!isObj(body) || ", "if (", [C.a04]),
  m("V1M-X26", "a non-list data is read", PORT, "!Array.isArray(body.data) || ", "", [C.a04]),
  m("V1M-X27", "a malformed next cursor is accepted", PORT, '!(body.next_cursor === null || typeof body.next_cursor === "string")', "false", [C.a04]),
  m("V1M-X28", "a malformed row is dropped silently", PORT, "      if (items.some((x) => x === null)) return UNAVAILABLE;\n", "", [C.a04]),
  m("V1M-X29", "an unconfigured service answers empty", PORT, "return { list: async () => UNAVAILABLE,", "return { list: async () => ({ ok: true, value: { items: [], next_cursor: null } }) as const,", [C.a02]),
  // the server composition and the WR-V2-1 wiring
  m("V1M-X30", "an unset base URL still reads", SERVER, "if (!baseUrl || config === null) return offlineTraces();\n  return httpTraces({ baseUrl,", "if (config === null) return offlineTraces();\n  return httpTraces({ baseUrl: baseUrl ?? \"\",", [C.w02]),
  m("V1M-X31", "a session without a token still sends", SESSION, "access_token ?? null", "access_token ?? \"eyJ0.x.y\"", [C.w01]),
  m("V1M-X32", "the Lab session cookie is not the one read", SESSION, "    cookieOptions: authCookieOptions(config),\n", "", [C.w01]),
  m("V1M-X33", "the request's cookies are not read", SESSION, "getAll: () => store.getAll()", "getAll: () => []", [C.w01]),
  m("V1M-X72", "the trace adapter carries the publishable key, not the session's token", SERVER, "token: sessionToken(config)", "token: async () => config.anonKey", [C.w01]),
  m("V1M-X73", "the trace adapter reads another family's old name", SERVER, "labApiUrl(env, \"traces\")", "labApiUrl(env, \"control\")", [C.w01]),
  m("V1M-X34", "the detail page is not wired to the adapter", WIRE, "  return { traces: labTraces() };\n}", '  return { traces: { detail: async () => ({ ok: false, reason: "unavailable" }) } } as never;\n}', [C.w01]),
  // URL state
  m("V1M-X35", "the cursor is reported as ignored", QUERY, '.filter((name) => name !== "cursor")', ".filter(() => true)", [C.q01]),
  m("V1M-X36", "ignored names are unsorted", QUERY, '.filter((name) => name !== "cursor").sort();', '.filter((name) => name !== "cursor");', [C.q01]),
  m("V1M-X37", "a repeated cursor is not named as such", QUERY, '  if (Array.isArray(value)) return { cursor: null, rejected: [{ name: "cursor", why: "given more than once" }], ignored };\n', "", [C.q02]),
  m("V1M-X38", "an empty cursor is refused", QUERY, 'value === undefined || value === ""', "value === undefined", [C.q01]),
  m("V1M-X39", "an oversized cursor is sent", QUERY, "value.length > MAX_CURSOR_CHARS || ", "", [C.q02]),
  m("V1M-X40", "the size bound is off by one", QUERY, "value.length > MAX_CURSOR_CHARS", "value.length >= MAX_CURSOR_CHARS", [C.q02]),
  m("V1M-X41", "the App's wider cursor charset", QUERY, "const CURSOR = /^[A-Za-z0-9_-]+$/;", "const CURSOR = /^[A-Za-z0-9._~+/=-]+$/;", [C.q02]),
  m("V1M-X42", "any cursor text is sent", QUERY, "!CURSOR.test(value)", "false", [C.q02]),
  m("V1M-X43", "a cursor is written into the link raw", QUERY, "`/requests?cursor=${encodeURIComponent(cursor)}`", "`/requests?cursor=${cursor}`", [C.q03]),
  m("V1M-X44", "the first page carries an empty cursor", QUERY, 'cursor === null ? "/requests"', 'cursor === null ? "/requests?cursor="', [C.q03, C.l04]),
  // the view
  m("V1M-X45", "the row link is not encoded", VIEW, "`/requests/${encodeURIComponent(d.request_id)}`", "`/requests/${d.request_id}`", [C.l01]),
  m("V1M-X46", "the duration is not completed minus started", VIEW, "Date.parse(d.completed_at) - Date.parse(d.started_at)", "Date.parse(d.completed_at)", [C.l01]),
  m("V1M-X47", "an open request reads as finished", VIEW, '"in progress"', '"—"', [C.l01]),
  m("V1M-X48", "the model shows without its revision", VIEW, "model: d.model_revision,", "model: d.model_id,", [C.l01]),
  m("V1M-X49", "a lost capture hides its reason", VIEW, "state === \"lost\" ? `${CONTENT_LABEL.lost}: ${reason}` : CONTENT_LABEL[state]", "CONTENT_LABEL[state]", [C.l02]),
  m("V1M-X50", "an unknown loss reason is invented", VIEW, "?? d.loss_reason;", '?? "unknown";', [C.l02]),
  m("V1M-X51", "a known loss reason is not explained", VIEW, 'queue_full: "the capture queue was full"', 'queue_full: "queue_full"', [C.l02]),
  m("V1M-X52", "shared content is not labelled as shared", VIEW, 'available: "Shared"', 'available: "Metadata only"', [C.l02]),
  m("V1M-X53", "expired content reads as metadata only", VIEW, 'expired: "Expired"', 'expired: "Metadata only"', [C.l02]),
  m("V1M-X54", "an uncaptured request reads as lost", VIEW, 'not_captured: "Not captured"', 'not_captured: "Lost"', [C.l02]),
  m("V1M-X55", "an empty first page with more to come reads as no traffic", VIEW, "items.length === 0 && next_cursor === null && cursor === null", "items.length === 0 && cursor === null", [C.l03]),
  m("V1M-X56", "an empty last page mid-walk reads as no traffic", VIEW, "items.length === 0 && next_cursor === null && cursor === null", "items.length === 0 && next_cursor === null", [C.l03]),
  m("V1M-X57", "the empty copy does not explain capture off", VIEW, "Requests made with capture off leave no trace record, so they are never listed here.", "Nothing has run yet.", [C.l03]),
  m("V1M-X58", "an empty page mid-walk says nothing", VIEW, "note: items.length === 0 ? LIST_COPY.gap : null,", "note: null,", [C.l03]),
  m("V1M-X59", "the next page is dropped", VIEW, "nextHref: next_cursor === null ? null : listHref(next_cursor),", "nextHref: null,", [C.l03, C.l04]),
  m("V1M-X60", "the first page offers a way back to itself", VIEW, "const firstHref = cursor === null ? null : listHref(null);", "const firstHref = listHref(null);", [C.l04, C.l05]),
  m("V1M-X61", "a lost workspace reads as a request not found", VIEW, 'result.reason === "not_found" ? LIST_COPY.not_found : ', "", [C.l05]),
  m("V1M-X62", "a refusal mid-walk has no way back", VIEW, '{ kind: "error", message: result.reason === "not_found" ? LIST_COPY.not_found : TRACE_COPY[result.reason], firstHref }', '{ kind: "error", message: result.reason === "not_found" ? LIST_COPY.not_found : TRACE_COPY[result.reason], firstHref: null }', [C.l05]),
  // the page and markup
  m("V1M-X63", "the provider comes from the URL", PAGE, "labTraces().list(workspace,", "labTraces().list({ ...workspace, providerId: String((await searchParams).provider_org_id) },", [C.p01]),
  m("V1M-X64", "the page ignores the cursor", PAGE, "labTraces().list(workspace, params.cursor)", "labTraces().list(workspace, null)", [C.p01]),
  m("V1M-X65", "refused parameters are silent", PAGE, "      <RejectedParams rejected={params.rejected} ignored={params.ignored} />\n", "", [C.p01]),
  m("V1M-X66", "a row is not a link to its request", TABLE, "<a href={r.href}>{r.requestId}</a>", "<span>{r.requestId}</span>", [C.p02]),
  m("V1M-X67", "a row hides its content state", TABLE, "{r.started} · {r.duration} · {r.model} · {r.content}", "{r.started} · {r.duration} · {r.model}", [C.p02]),
  m("V1M-X68", "there is no next page link", TABLE, "{view.nextHref !== null && <a href={view.nextHref}>Older requests</a>}", "", [C.p02]),
  m("V1M-X69", "a refusal is not announced", TABLE, '<p role="alert">{view.message}</p>', "<p>{view.message}</p>", [C.p02]),
  m("V1M-X70", "the list does not wrap on a phone", TABLE, 'const WRAP = { overflowWrap: "anywhere" } as const;', "const WRAP = {} as const;", [C.p02]),
  m("V1M-X71", "the error boundary offers no way out", ERROR, '<Link href="/requests">Start from the newest requests</Link>', "", [C.p02]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "V1M", mutants: MUTANTS }));
