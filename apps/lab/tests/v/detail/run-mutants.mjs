#!/usr/bin/env node
// V2's mutant runner (R32; LANE-RULES addendum) over tests/v/detail, on the shared Lab harness.
// Usage: node tests/v/detail/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["view", "journey", "page"].map((f) => `tests/v/detail/${f}.test.ts`);
const PORT = "components/traces/detail/port.ts";
const FAKE = "components/traces/detail/fake.ts";
const VIEW = "components/traces/detail/view.ts";
const PANELS = "components/traces/detail/panels.tsx";
const PAGE = "app/(provider)/requests/[id]/page.tsx";

const C = {
  d01: "V2-D01 metadata rows are named fields only; without a grant no organization, size or content is shown",
  d02: "V2-D02 content states are honest: not captured, lost, metadata only, expired or tombstoned, else available",
  d03: "V2-D03 no content is read or offered: an available record says content reads are not yet available",
  d05: "V2-D05 feedback is labelled with its stored provenance and is never presented as a calibration label",
  d06: "V2-D06 a missing request reads the same whether it is another provider's or not projected yet",
  d07: "V2-D07 the trace port fails closed whatever the environment says, and there is no content port",
  j01: "V2-J01 the provider's developer reads its own request; a granted one is available, an ungranted one metadata only",
  j02: "V2-J02 another provider, a viewer and a malformed id learn nothing; a missing request is the not-projected copy",
  p01: "V2-P01 the page reads as the session's workspace, and feedback shows whatever the trace read says",
  p02: "V2-P02 content is never read or offered: the panel states the record's content state, and ?content= is ignored",
  p03: "V2-P03 each feedback row shows its provenance (who) beside what and when",
};

const MUTANTS = [
  m("V2-X01", "a metadata-only row shows the organization and size", VIEW, 'if (d.access === "content") rows.push(', "if (true) rows.push(", [C.d01]),
  m("V2-X02", "the duration is not completed minus started", VIEW, "Date.parse(d.completed_at) - Date.parse(d.started_at)", "Date.parse(d.completed_at)", [C.d01]),
  m("V2-X03", "an unpriced request shows a rate card", VIEW, 'd.rate_card_version ?? "unpriced"', 'd.rate_card_version ?? "rc-0"', [C.d01]),
  m("V2-X04", "an open request reads as completed", VIEW, 'd.completed_at ?? "in progress"', 'd.completed_at ?? "—"', [C.d01]),
  m("V2-X05", "a lost capture hides its reason", VIEW, 'd.loss_reason === "none" ? "—" : d.loss_reason', '"—"', [C.d01]),
  m("V2-X06", "a metadata-mode capture is offered as content", VIEW, 'if (d.mode !== "full") return "not_captured";', 'if (d.mode === "off") return "not_captured";', [C.d02]),
  m("V2-X07", "a lost capture is offered as content", VIEW, '  if (d.loss_reason !== "none") return "lost";\n', "", [C.d02]),
  m("V2-X08", "no grant reads as expired content", VIEW, '  if (d.access === "metadata") return "metadata_only";\n', "", [C.d02, C.j01]),
  m("V2-X09", "tombstoned or expired content is offered", VIEW, 'return d.content_available ? "available" : "expired";', 'return "available";', [C.d02]),
  m("V2-X10", "an available record promises content on request", VIEW, "but content reads are not yet available in the Lab.", "and is shown when you ask for it.", [C.d03]),
  m("V2-X18", "feedback loses its author role", VIEW, "who: `${e.author_role} · ${e.channel}`", "who: e.channel", [C.d05]),
  m("V2-X19", "a thumbs down reads as up", VIEW, 'value === true ? "thumbs up" : "thumbs down"', '"thumbs up"', [C.d05]),
  m("V2-X20", "a rating loses its value", VIEW, "return `rating ${value}`;", 'return "rating";', [C.d05]),
  m("V2-X21", "customer feedback is offered as calibration", VIEW, "Customer feedback is never a calibration label.", "Customer feedback calibrates the judge.", [C.d05]),
  m("V2-X22", "a review refusal reads as no feedback", VIEW, "empty: FEEDBACK_COPY[result.reason] }", "empty: FEEDBACK_COPY.empty }", [C.d05]),
  m("V2-X23", "an empty review is blank", VIEW, "empty: rows.length === 0 ? FEEDBACK_COPY.empty : null", "empty: null", [C.d05]),
  m("V2-X24", "the feedback comment is dropped", VIEW, "text: e.comment,", "text: null,", [C.d05]),
  m("V2-X25", "a missing request says it is another provider's", VIEW, "No request with this id is visible in this workspace. A request made in the last few minutes may not be projected yet; try again shortly.", "That request belongs to another provider.", [C.d06]),
  m("V2-X26", "a preview flag turns a stand-in on", PORT, "  return { traces: labTraces() };\n}", '  return process.env.LAB_TRACES_PREVIEW === "1" ? ({ traces: { detail: async () => ({ ok: true, value: {} }) } } as never) : { traces: labTraces() };\n}', [C.d07]),
  m("V2-X11", "an always-unavailable content port comes back", PORT, "return { traces: labTraces() };", 'return { traces: labTraces(), content: { read: async () => ({ ok: false, reason: "unavailable" }) } } as never;', [C.d07]),
  m("V2-X28", "the trace read is not scoped to the provider", FAKE, "r.detail.request_id === requestId && r.providerId === actor.providerId", "r.detail.request_id === requestId", [C.j02]),
  m("V2-X29", "a viewer reads individual requests", FAKE, '    if (actor.role === "viewer") return { ok: false, reason: "denied" };\n', "", [C.j02]),
  m("V2-X35", "the page reads as a provider from the URL", PAGE, "const actor = { providerId: workspace.providerId, role: workspace.role };", "const actor = { providerId: id, role: workspace.role };", [C.p01]),
  m("V2-X36", "feedback waits on the projection", PAGE, "\n      <FeedbackPanel result={feedback} />\n", "\n      {trace.ok && <FeedbackPanel result={feedback} />}\n", [C.p01]),
  m("V2-X37", "a trace refusal is shown raw", PAGE, "{TRACE_COPY[trace.reason]}", "{trace.reason}", [C.p01]),
  m("V2-X12", "the page reads ?content= again", PAGE, "RequestDetail({ params }: PageProps", "RequestDetail({ params, searchParams }: PageProps", [C.p02]),
  m("V2-X13", "the content panel offers a Show content link", PANELS, '<p role="status">{CONTENT_COPY[contentState(detail)]}</p>', '<p role="status">{CONTENT_COPY[contentState(detail)]}</p>\n      <a href="?content=1">Show content</a>', [C.p02]),
  m("V2-X14", "the content panel ignores the record's state", PANELS, "{CONTENT_COPY[contentState(detail)]}", "{CONTENT_COPY.available}", [C.p02]),
  m("V2-X15", "the content state is not announced as a status", PANELS, '<p role="status">{CONTENT_COPY', "<p>{CONTENT_COPY", [C.p02]),
  m("V2-X41", "long content does not wrap on a phone", PANELS, 'whiteSpace: "pre-wrap", ', "", [C.p02]),
  m("V2-X42", "a feedback row drops its provenance", PANELS, "<strong>{r.what}</strong> · {r.who} · {r.when}", "<strong>{r.what}</strong> · {r.when}", [C.p03]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "V2", mutants: MUTANTS }));
