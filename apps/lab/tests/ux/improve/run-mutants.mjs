#!/usr/bin/env node
// UX-06/UX-09's mutant runner (R32) on the shared Lab harness (tests/l/shell/harness.mjs): each decision
// is one edit that a case it names must fail by assertion. Two lists: the static suites (seconds each)
// and the browser suite (each mutant serves its own copy of the synthetic harness: next dev + Chromium).
// Usage: node tests/ux/improve/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const STATIC = ["tests/ux/improve/datasets.test.ts", "tests/ux/improve/improve.test.ts", "tests/ux/improve/pages.test.ts"];
const BROWSER = ["tests/ux/improve/wizard.test.ts"];
const VIEWS = "lib/services/datasets/views.ts";
const D = "app/(provider)/datasets";
const FORMS = `${D}/forms.tsx`;
const TRAIN = "app/(provider)/training/page.tsx";
const TVIEW = "app/(provider)/training/view.ts";
const ANNOT = "app/(provider)/annotations/page.tsx";

const C = {
  v01: "UX06-V01 a percentage with at most two decimals is exact integer basis points; anything else is refused",
  v02: "UX06-V02 the split plan shows all three shares, holdout the remainder, and refuses more than 100% or a malformed share",
  v03: "UX06-V03 a preview counts only for the exact mapping and file it checked: editing either one invalidates it",
  v04: "UX06-V04 the import step: a file, then a mapping, then a current preview; only then can it be imported",
  v05: "UX06-V05 the spec template is the real schema with this workspace's ids; rights, licence and mapping are left for the provider",
  p01: "UX06-P01 the dataset library keeps its header in every state: a failed read is unavailable or denied, empty only for an answered empty list",
  p02: "UX06-P02 only a writer is offered the import, with a spec template whose import and dataset ids are minted at render for this workspace",
  p03: "UX06-P03 a version or import that cannot be read keeps its header and says so; derive and export only for a writer; a published import is not a release",
  k01: "UX06-K01 Import is off until a preview of the current file and mapping has run; the preview shows mapped and rejected rows as a head check",
  k02: "UX06-K02 an edit to the mapping or a new file after the preview invalidates it: the preview goes, Import is off until previewed again",
  k03: "UX06-K03 a refused preview is an alert and opens nothing; the import sends the reject policy the provider chose with the previewed file",
  k04: "UX06-K04 derive: exact percentages become basis points, the holdout is the shown remainder, and more than 100% cannot be sent",
  k05: "UX06-K05 at 320 px the import, derive and export forms reflow: the page never scrolls sideways",
  t01: "UX09-V01 only an ambiguous or stuck submission is offered the lookup, only to a writer, and the lookup never reads as a submit",
  t02: "UX09-V02 the gate copy says what stays off while the run records cannot be read, and that teacher labelling is read separately",
  j01: "UX09-J01 an ambiguous training submission: the page offers the lookup, never Submit; the lookup only looks up, and a found job moves on",
  j02: "UX09-J02 a teacher dry run sends and reserves nothing; only an administrator is offered the paid step, and an approval resumes without resending",
  q01: "UX09-P01 training: the run records sit behind their own gate; the heading and the teacher section stay, and no bundle is offered on records the page cannot read",
  q02: "UX09-P02 an unknown submission is offered the lookup form - its own run, the lookup-only op - and a writer's ref fields offer the dataset catalog",
  q03: "UX09-P03 review: the page keeps its header in every state, offers the dataset catalog to open, and the reviewer id is labelled as the member-list fallback",
};

const STATIC_MUTANTS = [
  // split percentages (views.ts)
  m("UXI-X01", "a third decimal is accepted", VIEWS, "(?:\\.(\\d{1,2}))?$/", "(?:\\.(\\d{1,3}))?$/", [C.v01]),
  m("UXI-X02", "the percentage goes through float arithmetic", VIEWS, 'const bp = Number(hit[1]) * 100 + Number((hit[2] ?? "").padEnd(2, "0"));', "const bp = Math.trunc(Number(text.trim()) * 100);", [C.v01]),
  m("UXI-X03", "one share may exceed 100%", VIEWS, "return bp <= 10_000 ? bp : null;", "return bp;", [C.v01]),
  m("UXI-X04", "a percentage drops its leading zero", VIEWS, 'String(bp % 100).padStart(2, "0")', "String(bp % 100)", [C.v01]),
  m("UXI-X05", "train and validation may exceed 100%", VIEWS, "if (trainBp + validationBp > 10_000) return", "if (trainBp + validationBp > 20_000) return", [C.v02]),
  m("UXI-X06", "the holdout is not the remainder", VIEWS, "holdoutBp: 10_000 - trainBp - validationBp", "holdoutBp: 0", [C.v02]),
  m("UXI-X07", "a malformed share is not refused", VIEWS, "if (trainBp === null || validationBp === null) return", "if (trainBp === null && validationBp === null) return", [C.v02]),
  // the preview's currency and the steps
  m("UXI-X08", "the preview ignores a re-saved or resized file", VIEWS, "file?.size ?? null, file?.lastModified ?? null", "null, null", [C.v03]),
  m("UXI-X09", "the preview ignores the mapping", VIEWS, "JSON.stringify([spec, file?.name", 'JSON.stringify(["", file?.name', [C.v03]),
  m("UXI-X10", "any earlier preview opens Import", VIEWS, 'return previewed === previewKey(spec, file) ? "import" : "validate";', 'return previewed !== null ? "import" : "validate";', [C.v04]),
  m("UXI-X11", "there is no Source step", VIEWS, '  if (file === null) return "source";\n', "", [C.v04]),
  m("UXI-X12", "a blank mapping skips to Validate", VIEWS, '  if (spec.trim() === "") return "mapping";\n', "", [C.v04]),
  // the template
  m("UXI-X13", "the template authorises a grant", VIEWS, 'grant_ref: "",', 'grant_ref: "lab:grant:any",', [C.v05]),
  m("UXI-X14", "the template claims customer content", VIEWS, 'ownership: "provider_owned"', 'ownership: "customer"', [C.v05]),
  m("UXI-X15", "the template names a content field for the provider", VIEWS, 'fields: { content: "" }', 'fields: { content: "text" }', [C.v05]),
  // dataset pages
  m("UXI-X16", "a failed library read renders as the list", `${D}/page.tsx`, "{!versions.ok ? (", "{false ? (", [C.p01]),
  m("UXI-X17", "an answered list is never empty-stated", `${D}/page.tsx`, ') : versions.value.length === 0 ? (\n        <ServiceState state="empty"', ') : versions.ok === false ? (\n        <ServiceState state="empty"', [C.p01]),
  m("UXI-X18", "a viewer is offered the import", `${D}/page.tsx`, '{writer ? (\n        <section id="import"', '{true ? (\n        <section id="import"', [C.p02]),
  m("UXI-X19", "the import id is not minted at render", `${D}/page.tsx`, "importId: randomUUID(), datasetId", "importId: workspace.providerId, datasetId", [C.p02]),
  m("UXI-X20", "an unreadable version loses its header", `${D}/[ref]/page.tsx`, '        <PageHeader breadcrumb={CRUMBS} title="Dataset version" />\n', "", [C.p03]),
  m("UXI-X21", "a viewer is offered derive and export", `${D}/[ref]/page.tsx`, '{holds(workspace.role, "run_evaluation") && (', "{true && (", [C.p03]),
  m("UXI-X22", "a running import reads as a proven outcome", `${D}/imports/[id]/page.tsx`, 'progress: "info"', 'progress: "success"', [C.p03]),
  m("UXI-X23", "a published import is not distinguished from a release", `${D}/imports/[id]/page.tsx`, " It is not a public release.", "", [C.p03]),
  // training
  m("UXI-X24", "every unfinished run is offered the lookup", TVIEW, '(state === "ambiguous" || state === "submitting")', '(state !== "completed")', [C.t01, C.j01]),
  m("UXI-X25", "a viewer is offered the lookup", TVIEW, "writer && (state ===", "(state ===", [C.t01]),
  m("UXI-X26", "the lookup is labelled as a resubmit", TVIEW, '"Look up the outcome (never submits again)"', '"Submit again"', [C.t01]),
  m("UXI-X27", "the gate promises the actions", TVIEW, "preparing a bundle and importing a checkpoint are not offered", "preparing a bundle and importing a checkpoint are offered", [C.t02]),
  m("UXI-X28", "a developer is offered the paid teacher step", "lib/services/pipelines/view.ts", 'approvable: b.approval === null && b.withinBudget && holds(role, "manage_members")', 'approvable: b.approval === null && b.withinBudget && holds(role, "run_evaluation")', [C.j02]),
  m("UXI-X29", "a bundle is offered on unread records", TRAIN, "{!gated && (\n            <form action={prepareTraining}>", "{(\n            <form action={prepareTraining}>", [C.q01]),
  m("UXI-X30", "the gate does not say what stays off", TRAIN, '      {gated && <p role="note">{GATED_COPY}</p>}\n', "", [C.q01]),
  m("UXI-X31", "a checkpoint import is offered on unread records", TRAIN, "{runs.ok && runs.value.length > 0 && (", "{runs.value.length > 0 && (", [C.q01]),
  m("UXI-X32", "the lookup posts a cancel", TRAIN, '<input type="hidden" name="op" value="submit" />', '<input type="hidden" name="op" value="cancel" />', [C.q02]),
  m("UXI-X33", "the lookup ignores the session's role", TRAIN, 'const writer = holds(workspace.role, "run_evaluation");\n  return (', "const writer = true;\n  return (", [C.q02]),
  m("UXI-X34", "a viewer's page reads the dataset catalog", TRAIN, "writer ? (await datasetsPort()).versions(workspace.providerId) : null,", "(await datasetsPort()).versions(workspace.providerId),", [C.q02]),
  m("UXI-X35", "the bundle's dataset field offers no catalog", TRAIN, '<input name="datasetRef" required list="dataset-versions" placeholder="lab:dataset:…@sha256:…" /></label>\n              <label>Export format', '<input name="datasetRef" required placeholder="lab:dataset:…@sha256:…" /></label>\n              <label>Export format', [C.q02]),
  // review
  m("UXI-X36", "the review page is titled by its route", ANNOT, 'title="Review"', 'title="Annotations"', [C.q03]),
  m("UXI-X37", "the open field offers no catalog", ANNOT, 'required list="dataset-versions" defaultValue', "required defaultValue", [C.q03]),
  m("UXI-X38", "the reviewer id is not explained", ANNOT, "No member list is read here yet: enter the member&apos;s user id.", "Enter an id.", [C.q03]),
];

const BROWSER_MUTANTS = [
  m("UXI-X40", "Import is open without a current preview", FORMS, "disabled={step !== \"import\"}", "disabled={false}", [C.k01]),
  m("UXI-X41", "the import is lenient by default", FORMS, 'name="accept_rejects" defaultValue="off"', 'name="accept_rejects" defaultValue="on"', [C.k01]),
  m("UXI-X42", "a stale preview is still shown", FORMS, "{checked.status === \"ok\" && !stale && (", "{checked.status === \"ok\" && (", [C.k02]),
  m("UXI-X43", "the form submits natively and loses its state", FORMS, "  event.preventDefault();\n", "", [C.k02]),
  m("UXI-X44", "the file input does not count as an edit", FORMS, "onChange={(e) => setFile(meta(e.currentTarget.files?.[0]))}", "onChange={(e) => { const next = meta(e.currentTarget.files?.[0]); setFile((f) => f ?? next); }}", [C.k02]),
  m("UXI-X45", "Import runs the preview again", FORMS, 'by?.dataset.step === "import" ? startAction : previewAction', "previewAction", [C.k03]),
  m("UXI-X46", "a refused preview is not announced", FORMS, '<div role="alert" className={styles.problem}>', "<div className={styles.problem}>", [C.k03]),
  m("UXI-X47", "the derive sends the typed percentage, not basis points", FORMS, 'name="train_bp" value={plan.ok ? plan.trainBp : ""}', 'name="train_bp" value={train}', [C.k04]),
  m("UXI-X48", "an over-100% split can be sent", FORMS, "<Button type=\"submit\" pending={pending} disabled={!plan.ok}>", "<Button type=\"submit\" pending={pending}>", [C.k04]),
  m("UXI-X49", "an invalid share is not marked", FORMS, "const error = touched && !plan.ok ? plan.message : null;", "const error = null;", [C.k04]),
  m("UXI-X50", "the holdout is not shown", FORMS, " · Holdout ${bpPercent(plan.holdoutBp)} (the remainder)", "", [C.k04]),
  m("UXI-X51", "a long mapped row widens the page", FORMS, '<span className="lab-id">{JSON.stringify(row.mapped)}</span>', "<span>{JSON.stringify(row.mapped)}</span>", [C.k05]),
];

const a = await runMutants({ suite: STATIC, prefix: "UX0[69]", mutants: STATIC_MUTANTS });
const b = await runMutants({ suite: BROWSER, prefix: "UX06", mutants: BROWSER_MUTANTS });
process.exit(a || b);
