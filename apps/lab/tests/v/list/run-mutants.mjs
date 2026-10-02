#!/usr/bin/env node
// V1M's mutant runner (R32; LANE-RULES addendum) over tests/v/list, on the shared Lab harness. Moved from
// the App's tests/v runner; stack.test.ts (the real route) is outside it, like V2's feedback-postgrest.
// UX-05 moved the list view and page cases (V1M-L*/P*, mutants X45-X71) to tests/ux/requests (UX05-L*).
// Usage: node tests/v/list/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["adapter", "wiring", "query"].map((f) => `tests/v/list/${f}.test.ts`);
const PORT = "lib/services/traces/port.ts";
const SERVER = "lib/services/traces/server.ts";
const SESSION = "lib/auth/session.ts"; // LAB-05: the one read-only session client
const WIRE = "components/traces/detail/port.ts";
const QUERY = "components/traces/list/query.ts";

const C = {
  a01: "V1M-A01 reads as the session's workspace with the user's own token: the list and one request",
  a02: "V1M-A02 refusals map to denied, not_found or unavailable, and no token sends nothing",
  a03: "V1M-A03 only the route's named fields are kept: a metadata row never carries an organization, key, size or content",
  a04: "V1M-A04 an answer that is not the route's shape is unavailable, never a partial record",
  a05: "V1M-A05 the list's filters are the route's: each one set is sent by name, an unset one is not sent, and the cursor rides along",
  w01: "V1M-W01 the detail page's trace port is the Lab adapter, reading with the session's access token",
  w02: "V1M-W02 unset LAB_API_URL or Lab config is off: every read unavailable, nothing sent",
  q01: "V1M-Q01 the route's cursor passes through exactly; every other name is ignored and reported, sorted",
  q02: "V1M-Q02 a repeated, oversized or non-base64url cursor is refused with its reason, never sent",
  q03: "V1M-Q03 page links are /requests plus the cursor, and the first page carries none",
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
  // WR-UX05-1 (AP-07c): filters on the wire, inline content on a granted detail only, the new fields typed
  m("V1M-X74", "the filter is not sent", PORT, "...narrowed, ", "", [C.a05]),
  m("V1M-X75", "an unset filter is sent as null", PORT, ".filter(([, value]) => value !== null)", ".filter(() => true)", [C.a05]),
  m("V1M-X76", "a granted detail drops its inline content", PORT, "listRow ? GRANTED : GRANTED_DETAIL", "GRANTED", [C.a03]),
  m("V1M-X77", "a list row must carry content", PORT, "listRow ? GRANTED : GRANTED_DETAIL", "GRANTED_DETAIL", [C.a01, C.a03]),
  m("V1M-X78", "the access state is dropped", PORT, "access_state: S, ", "", [C.a01, C.a03]),
  m("V1M-X79", "elapsed_ms may be text", PORT, "elapsed_ms: NN,", 'elapsed_ms: ["number", "null", "string"],', [C.a04]),
  m("V1M-X80", "the request schema version may be text", PORT, "request_schema_version: N,", 'request_schema_version: ["number", "string"],', [C.a04]),
  m("V1M-X81", "a granted detail may carry non-text content", PORT, "{ ...GRANTED, content: SN }", '{ ...GRANTED, content: ["string", "null", "object"] }', [C.a04]),
  // the server composition and the WR-V2-1 wiring
  m("V1M-X30", "an unconfigured Lab still reads", SERVER, "if (config === null) return offlineTraces();", 'if (config === null) return httpTraces({ baseUrl: "http://x.invalid", token: sessionToken });', [C.w02]),
  m("V1M-X31", "a session without a token still sends", SESSION, "get(AUTH_COOKIE)?.value || null", "get(AUTH_COOKIE)?.value || \"eyJ0.x.y\"", [C.w01]),
  m("V1M-X32", "the Lab session cookie is not the one read", SESSION, "get(AUTH_COOKIE)?.value || null", "get(REFRESH_COOKIE)?.value || null", [C.w01]),
  m("V1M-X72", "the trace adapter carries the publishable key, not the session's token", SERVER, "token: sessionToken", "token: async () => \"anon\"", [C.w01]),
  m("V1M-X34", "the detail page is not wired to the adapter", WIRE, "  return { traces: labTraces() };\n}", '  return { traces: { detail: async () => ({ ok: false, reason: "unavailable" }) } } as never;\n}', [C.w01]),
  // URL state
  m("V1M-X35", "the cursor is reported as ignored", QUERY, ".filter((name) => !KNOWN.has(name))", ".filter(() => true)", [C.q01]),
  m("V1M-X36", "ignored names are unsorted", QUERY, ".filter((name) => !KNOWN.has(name)).sort();", ".filter((name) => !KNOWN.has(name));", [C.q01]),
  m("V1M-X37", "a repeated cursor is not named as such", QUERY, 'if (Array.isArray(value)) rejected.push({ name, why: "given more than once" });', "if (Array.isArray(value)) return null;", [C.q02]),
  m("V1M-X38", "an empty cursor is refused", QUERY, 'value === undefined || value === ""', "value === undefined", [C.q01]),
  m("V1M-X39", "an oversized cursor is sent", QUERY, "cursor.length > MAX_CURSOR_CHARS || ", "", [C.q02]),
  m("V1M-X40", "the size bound is off by one", QUERY, "cursor.length > MAX_CURSOR_CHARS", "cursor.length >= MAX_CURSOR_CHARS", [C.q02]),
  m("V1M-X41", "the App's wider cursor charset", QUERY, "const CURSOR = /^[A-Za-z0-9_-]+(\\.[0-9a-f]+)?$/;", "const CURSOR = /^[A-Za-z0-9._~+/=-]+$/;", [C.q02]),
  m("V1M-X42", "any cursor text is sent", QUERY, "!CURSOR.test(cursor)", "false", [C.q02]),
  m("V1M-X43", "a cursor is written into the link raw", QUERY, "const search = query.toString();", "const search = decodeURIComponent(query.toString());", [C.q03]),
  m("V1M-X44", "the first page carries an empty cursor", QUERY, 'search === "" ? "/requests"', 'search === "" ? "/requests?cursor="', [C.q03]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "V1M", mutants: MUTANTS }));
