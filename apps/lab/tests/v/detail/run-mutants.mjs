#!/usr/bin/env node
// V2's mutant runner (R32; LANE-RULES addendum) over tests/v/detail, on the shared Lab harness.
// UX-05 moved the metadata/content-state and page cases (V2-D01..D03, V2-P*; mutants X01-X15, X35-X42)
// to tests/ux/requests (UX05-D*).
// Usage: node tests/v/detail/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["view", "journey"].map((f) => `tests/v/detail/${f}.test.ts`);
const PORT = "components/traces/detail/port.ts";
const FAKE = "components/traces/detail/fake.ts";
const VIEW = "components/traces/detail/view.ts";

const C = {
  d05: "V2-D05 feedback is labelled with its stored provenance and is never presented as a calibration label",
  d06: "V2-D06 a missing request reads the same whether it is another provider's or not projected yet",
  d07: "V2-D07 the trace port fails closed whatever the environment says, and there is no content port",
  j01: "V2-J01 the provider's developer reads its own request; a granted one is available, an ungranted one metadata only",
  j02: "V2-J02 another provider, a viewer and a malformed id learn nothing; a missing request is the not-projected copy",
};

const MUTANTS = [
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
];

process.exit(await runMutants({ suite: SUITE, prefix: "V2", mutants: MUTANTS }));
