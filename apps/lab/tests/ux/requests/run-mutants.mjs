#!/usr/bin/env node
// UX-05's mutant runner (R32) on the shared Lab harness (tests/l/shell/harness.mjs): every decision the
// request list and request page make is one edit that a UX05 case it names must fail by assertion.
// Usage: node tests/ux/requests/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["tests/ux/requests/requests.test.ts"];
const VIEW = "components/traces/list/view-model.ts";
const QUERY = "components/traces/list/query.ts";
const TABLE = "components/traces/list/trace-table.tsx";
const DVIEW = "components/traces/detail/view.ts";
const PANELS = "components/traces/detail/panels.tsx";
const PAGE = "app/(provider)/requests/page.tsx";
const DPAGE = "app/(provider)/requests/[id]/page.tsx";
const ERROR = "app/(provider)/requests/error.tsx";

const C = {
  l01: "UX05-L01 the list reads as the session workspace with the URL's filter and cursor; a URL provider is ignored and said so",
  l02: "UX05-L02 a row shows named metadata: id link, readable start, server elapsed, model, revision, mode, access and loss; never an organization, size or content",
  l03: "UX05-L03 every access state has its own label; an unknown state reads as unknown, never as shared",
  l04: "UX05-L04 page links carry the server's cursor and the active filter; only a later page offers the newest",
  l05: "UX05-L05 the filter is the server's: a row outside it makes the page unavailable, never a filtered-looking list",
  l06: "UX05-L06 empty says no requests were returned; filtered-empty says so and offers the whole list; an empty page mid-walk is a gap",
  l07: "UX05-L07 T09: an unavailable trace service keeps the heading and offers Try again on the same URL; denied and not found are their own states",
  l08: "UX05-L08 a refused URL value is reported and never sent: a repeated or oversized filter, a cursor that is not ours",
  d01: "UX05-D01 metadata only: the content region says content is not shared, and nothing of a carried body is rendered or read",
  d02: "UX05-D02 expired and revoked content disappears: each state's copy, the allowed metadata kept, no body",
  d03: "UX05-D03 partial capture is marked and its body is not presented as complete",
  d04: "UX05-D04 shared content is text with its provenance (organization, grant, size); markup inside it is never executed",
  d05: "UX05-D05 a request outside the session workspace is not found: the unprojected copy, no metadata, read as the session workspace",
  d06: "UX05-D06 T09: an unavailable trace read keeps the heading and offers Try again, and feedback still shows",
  d07: "UX05-D07 the summary names request, model, revision and serving identity with copy controls, the pins, and links to that serving version's requests",
  e01: "UX05-E01 the error boundary keeps the heading and offers the newest requests",
};

const MUTANTS = [
  // the list page
  m("UX05-X01", "the provider comes from the URL", PAGE, "traces.list(workspace,", "traces.list({ ...workspace, providerId: String((await searchParams).provider_org_id) },", [C.l01]),
  m("UX05-X02", "the filter is not sent to the route", PAGE, "traces.list(workspace, params.cursor, params.filter)", "traces.list(workspace, params.cursor, { model_id: null, serving_version_id: null })", [C.l01]),
  m("UX05-X03", "the cursor is dropped", PAGE, "traces.list(workspace, params.cursor,", "traces.list(workspace, null,", [C.l01]),
  m("UX05-X04", "refused and ignored parameters are silent", PAGE, "      <RejectedParams rejected={params.rejected} ignored={params.ignored} />\n", "", [C.l01]),
  m("UX05-X05", "the page does not say whose requests these are", PAGE, "purpose=\"Requests on this workspace's deployments,", 'purpose="Recent traffic,', [C.l01]),
  m("UX05-X06", "the heading is not the page's name", PAGE, '<PageHeader title="Requests"', '<PageHeader title="Request log"', [C.l01, C.l07]),
  // rows
  m("UX05-X07", "the row link is not encoded", VIEW, "`/requests/${encodeURIComponent(d.request_id)}`", "`/requests/${d.request_id}`", [C.l02]),
  m("UX05-X08", "an unfinished request reads as instant", VIEW, '(ms === null ? "Unfinished"', '(ms === null ? "0 ms"', [C.l02, C.d07]),
  m("UX05-X09", "instants are shown raw", VIEW, '`${at.toISOString().slice(0, 19).replace("T", " ")} UTC`', "iso", [C.l02, C.d07]),
  m("UX05-X10", "a row hides its loss reason", VIEW, "loss: lossDetail(d.loss_reason),", "loss: null,", [C.l02]),
  m("UX05-X11", "a known loss reason is not explained", VIEW, 'queue_full: "the capture queue was full"', 'queue_full: "queue_full"', [C.l02]),
  m("UX05-X12", "an unknown loss reason is invented", VIEW, "?? reason)", '?? "unknown")', [C.l02]),
  m("UX05-X13", "the start has no machine-readable instant", TABLE, "<time dateTime={r.startedAt}>", "<time>", [C.l02]),
  m("UX05-X14", "every row reads as shared", TABLE, "<Badge tone={r.access.tone}>{r.access.label}</Badge>", '<Badge tone="info">Shared</Badge>', [C.l02]),
  m("UX05-X15", "the table hides the loss reason", TABLE, "{r.loss !== null && <div className={styles.muted}>Lost: {r.loss}</div>}", "", [C.l02]),
  // access labels
  m("UX05-X16", "an unknown access state reads as shared", VIEW, "?? UNKNOWN;", "?? ACCESS_LABEL.content;", [C.l03]),
  m("UX05-X17", "a partial capture reads as shared", VIEW, 'partial: { label: "Partial capture", tone: "warning" }', 'partial: { label: "Shared", tone: "info" }', [C.l03]),
  m("UX05-X18", "a withdrawn grant reads as never given", VIEW, 'revoked: { label: "Access revoked"', 'revoked: { label: "Metadata only"', [C.l03]),
  m("UX05-X19", "shared content reads as a health verdict", VIEW, 'content: { label: "Shared", tone: "info" }', 'content: { label: "Shared", tone: "success" }', [C.l03]),
  // paging and filters
  m("UX05-X20", "the next page drops the filter", VIEW, "listHref(next_cursor, filter)", "listHref(next_cursor)", [C.l04]),
  m("UX05-X21", "the first page offers a way back to itself", VIEW, "const firstHref = cursor === null ? null : listHref(null, filter);", "const firstHref = listHref(null, filter);", [C.l04]),
  m("UX05-X22", "the way back drops the filter", VIEW, "const firstHref = cursor === null ? null : listHref(null, filter);", "const firstHref = cursor === null ? null : listHref(null);", [C.l04]),
  m("UX05-X23", "a filtered cursor is refused", QUERY, "(\\.[0-9a-f]+)?$/;", "$/;", [C.l04]),
  m("UX05-X24", "links drop the filter", QUERY, "for (const name of FILTERS) if (filter[name] !== null) query.set(name, filter[name]);", "", [C.l04]),
  m("UX05-X25", "a row outside the filter is listed anyway", VIEW, '  if (items.some((d) => outside(d, filter))) return refused("unavailable");\n', "", [C.l05]),
  m("UX05-X26", "the serving-version filter is not checked", VIEW, " || (f.serving_version_id !== null && d.serving_version_id !== f.serving_version_id)", "", [C.l05]),
  m("UX05-X27", "a filtered empty reads as an empty workspace", VIEW, "title: filtered ? LIST_COPY.filteredEmpty : LIST_COPY.empty", "title: LIST_COPY.empty", [C.l06]),
  m("UX05-X28", "a filtered empty offers no way to the whole list", VIEW, "clearHref: filtered ? listHref(null) : null", "clearHref: null", [C.l06]),
  m("UX05-X29", "an empty page mid-walk reads as no traffic", VIEW, "items.length === 0 && next_cursor === null && cursor === null", "items.length === 0 && cursor === null", [C.l06]),
  m("UX05-X30", "the empty copy does not explain capture off", VIEW, '"Requests made with capture off leave no trace record, so they are never listed here."', '"Nothing has run yet."', [C.l06]),
  m("UX05-X31", "the empty state has no Show all link", TABLE, "action={view.clearHref !== null ? <Link href={view.clearHref}>Show all requests</Link> : undefined}", "action={undefined}", [C.l06]),
  // refusals
  m("UX05-X32", "an unavailable list offers no retry", TABLE, 'view.state === "unavailable" ? (', "false ? (", [C.l07]),
  m("UX05-X33", "the retry loses the filter", VIEW, "retryHref: listHref(cursor, filter)", 'retryHref: "/requests"', [C.l07]),
  m("UX05-X34", "a refused read reads as an empty workspace", VIEW, "if (!result.ok) return refused(result.reason);", "if (!result.ok) return { kind: \"empty\", title: LIST_COPY.empty, message: LIST_COPY.emptyWhy, clearHref: null };", [C.l07]),
  m("UX05-X35", "every refusal reads as unavailable", VIEW, "if (!result.ok) return refused(result.reason);", 'if (!result.ok) return refused("unavailable");', [C.l07]),
  m("UX05-X36", "the state is not the refusal's", TABLE, "state={view.state}", 'state="unavailable"', [C.l07]),
  // URL values
  m("UX05-X37", "an oversized filter is sent", QUERY, "else if (value.length > max)", "else if (false)", [C.l08]),
  m("UX05-X38", "the filter bound is off by one", QUERY, "export const MAX_FILTER_CHARS = 200;", "export const MAX_FILTER_CHARS = 199;", [C.l08]),
  m("UX05-X39", "an empty filter is a filter", QUERY, 'value === undefined || value === ""', "value === undefined", [C.l08]),
  m("UX05-X40", "a filter name is reported as ignored", QUERY, 'new Set<string>(["cursor", ...FILTERS])', 'new Set<string>(["cursor"])', [C.l01]),
  m("UX05-X41", "a cursor that is not ours is sent", QUERY, "    cursor = null;\n", "", [C.l08]),
  // one request: content
  m("UX05-X42", "content access ignores the state", DVIEW, 'if (state === "content" || state === "partial") {', 'if (state !== "not_captured") {', [C.d01, C.d02]),
  m("UX05-X43", "a metadata record claiming content shows a body", DVIEW, '    if (d.access !== "content") return { kind: "none", copy: CONTENT_COPY.unknown };\n', "", [C.d01]),
  m("UX05-X44", "the page reads content beside the record", DPAGE, "await Promise.all([traces.detail(workspace, id),", "await Promise.all([traces.detail(workspace, id).then(async (t) => (await traces.detail(workspace, `${id}/content`), t)),", [C.d01]),
  m("UX05-X45", "unreadable content is an empty body", DVIEW, '    if (typeof d.content !== "string") return { kind: "none", copy: CONTENT_COPY.unread };\n', "", [C.d04]),
  m("UX05-X46", "a partial body is not marked", DVIEW, 'partial: state === "partial"', "partial: false", [C.d03]),
  m("UX05-X47", "the partial note is hidden", PANELS, "{region.partial && (", "{false && (", [C.d03]),
  m("UX05-X48", "the body is rendered as HTML", PANELS, "<pre className={styles.content}>{region.text}</pre>", "<pre className={styles.content} dangerouslySetInnerHTML={{ __html: region.text }} />", [C.d04]),
  m("UX05-X49", "the provenance drops the organization", DVIEW, '["Shared by organization", d.grantor_org_id], ', "", [C.d04]),
  m("UX05-X50", "expired content reads as never shared", DVIEW, 'expired: "This request\'s content is past its retention or was deleted, so it is not shown.",', 'expired: "Content isn\'t shared with this workspace.",', [C.d02]),
  m("UX05-X51", "a revoked grant reads as never shared", DVIEW, 'revoked: "Content access was revoked or has lapsed:', 'revoked: "Content isn\'t shared with this workspace:', [C.d02]),
  m("UX05-X52", "a lost capture hides why", DVIEW, "`${CONTENT_COPY.not_captured} The capture was lost: ${why}.`", "CONTENT_COPY.not_captured", [C.d02]),
  m("UX05-X53", "the metadata-only copy promises content", DVIEW, 'metadata: "Content isn\'t shared with this workspace:', 'metadata: "Content is on its way:', [C.d01]),
  // one request: refusals and identity
  m("UX05-X54", "the detail reads as a provider from the URL", DPAGE, "traces.detail(workspace, id)", "traces.detail({ ...workspace, providerId: id }, id)", [C.d05]),
  m("UX05-X55", "a missing request says it is another provider's", DVIEW, "A request made in the last few minutes may not be projected yet; try again shortly.", "That request belongs to another provider.", [C.d05]),
  m("UX05-X56", "a refused read shows no state", DPAGE, "{!trace.ok && <TraceRefused", "{false && <TraceRefused", [C.d05, C.d06]),
  m("UX05-X57", "a not-found request offers no way back", PANELS, '<Link href="/requests">Back to requests</Link>', "null", [C.d05]),
  m("UX05-X58", "an unavailable request offers no retry", PANELS, 'reason === "unavailable" ? (', "false ? (", [C.d06]),
  m("UX05-X59", "the retry leaves the request", DPAGE, "retryHref={`/requests/${encodeURIComponent(id)}`}", 'retryHref="/requests"', [C.d06]),
  m("UX05-X60", "feedback waits on the trace read", DPAGE, "\n      <FeedbackPanel result={feedback} />\n", "\n      {trace.ok && <FeedbackPanel result={feedback} />}\n", [C.d06]),
  m("UX05-X61", "the serving version cannot be copied", PANELS, '<Id value={detail.serving_version_id} label="Copy serving version id" />', "<code>{detail.serving_version_id}</code>", [C.d07]),
  m("UX05-X62", "the serving version does not link to its requests", PANELS, "listHref(null, { ...NO_FILTER, serving_version_id: detail.serving_version_id })", "listHref(null)", [C.d07]),
  m("UX05-X63", "an unpriced request shows a rate card", DVIEW, 'd.rate_card_version ?? "unpriced"', 'd.rate_card_version ?? "rc-0"', [C.d07]),
  m("UX05-X64", "the price pin is dropped", DVIEW, '    ["Price version", d.price_version],\n', "", [C.d07]),
  m("UX05-X65", "the request page has no breadcrumb", DPAGE, '<PageHeader title="Request" breadcrumb={[{ href: "/requests", label: "Requests" }]} />', '<PageHeader title="Request" />', [C.d07]),
  // the error boundary
  m("UX05-X66", "the error boundary offers no way out", ERROR, 'action={<Link href="/requests">Start from the newest requests</Link>}', "action={undefined}", [C.e01]),
  m("UX05-X67", "the error boundary drops the heading", ERROR, '<PageHeader title="Requests" />', "", [C.e01]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "UX05", mutants: MUTANTS }));
