#!/usr/bin/env node
// UX-10's mutant runner (R32) on the shared Lab harness (tests/l/shell/harness.mjs): each release and
// optimization evidence decision is one edit that a UX10 case it names must fail by assertion. The page
// suite lays pages out in Chromium, so a run takes a few minutes.
// Usage: node tests/ux/releases/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["tests/ux/releases/view.test.ts", "tests/ux/releases/pages.test.ts"];
const VIEW = "lib/services/releases/view.ts";
const PORT = "lib/services/releases/port.ts";
const R = "app/(provider)/releases";
const PAGE = `${R}/page.tsx`;
const LOADING = `${R}/loading.tsx`;
const VARIANTS = `${R}/variants.tsx`;

const C = {
  p01: "UX10-P01 an administrator sees each release's summary, its serving proof and evidence, and proposes against the loaded fence",
  p02: "UX10-P02 a viewer and a developer read the same evidence with no proposal and the reason",
  p03: "UX10-P03 publication requests show the operator's decision beside the revision's serving proof, never one for the other",
  p04: "UX10-P04 every section state: empty, unavailable and denied each name themselves; one failing section leaves the other",
  p05: "UX10-P05 the loading state keeps the heading and announces itself",
  p06: "UX10-P06 ?refused=conflict asks for review again; an unknown reason shows nothing; the preview note only while previewing",
  p07: "UX10-P07 the optimization list: scoped identities, Not recorded, not comparable, its own serving version; every state",
  l01: "UX10-L01 long identifiers wrap: at 390, 768 and 1440 nothing scrolls sideways and every control Tab reaches is on screen",
  v01: "UX10-V01 no progress is No measurement yet, never zero traffic; a unit-refused release keeps its refusal",
  v02: "UX10-V02 the current serving proof is the latest observation only: an approved expansion is never proof",
  v03: "UX10-V03 a proposal summary names the policy and the revision the server loaded, and says an operator decides",
  v04: "UX10-V04 viewer and developer get no proposal and are told an administrator proposes; an administrator gets no note",
  v05: "UX10-V05 a stale fence asks for review again; any other ?refused= is the fixed copy, an unknown one nothing",
  s01: "UX10-S01 a refused read is denied only for 403/denied; anything else is unavailable, never empty",
  u01: "UX10-U01 readiness: ready is proof with its time; not ready names the reasons; none recorded and unreachable differ",
  u02: "UX10-U02 a publication request shows its decision beside its revision's proof; an approval without proof says so",
  u03: "UX10-U03 the reads go through the generated client as the workspace: one readiness read per distinct revision",
  o01: "UX10-O01 an identity stored before it was recorded reads Not recorded, and its figures are not a comparison",
  o02: "UX10-O02 a variant measured on other hardware is not comparable: no ratio, no claim",
  o03: "UX10-O03 an optimization is its own serving version: the base is unchanged, and the report digest is shown",
};

const MUTANTS = [
  // the view: releases
  m("UX10-X01", "no progress reads as R4's traffic line", VIEW, "traffic: r.progress === null && r.refused === undefined ? NO_MEASUREMENT : rows[i].traffic,", "traffic: rows[i].traffic,", [C.v01]),
  m("UX10-X02", "a unit-refused release loses its refusal", VIEW, "r.progress === null && r.refused === undefined ?", "r.progress === null ?", [C.v01]),
  m("UX10-X03", "the observed window is dropped", VIEW, "observed: r.progress ? `through ${r.progress.observedUntil}` : NO_MEASUREMENT,", "observed: NO_MEASUREMENT,", [C.v01, C.p01]),
  m("UX10-X04", "an approval counts as serving proof", VIEW, "  const live = r.progress;\n  if (live === null) {", '  if (r.state === "approved") return { tone: "success", label: "Candidate healthy", text: "approved" };\n  const live = r.progress;\n  if (live === null) {', [C.v02, C.p02]),
  m("UX10-X05", "an unhealthy candidate reads healthy", VIEW, "return live.candidateHealthy\n", "return true\n", [C.v02]),
  m("UX10-X06", "a unit-refused proof reads as nothing measured", VIEW, 'return r.refused === "unit_refused"\n', "return false\n", [C.v01]),
  m("UX10-X07", "the summary names the policy version, not the loaded fence", VIEW, "at policy revision ${rows[i].fence}", "at policy revision ${r.version}", [C.v03]),
  m("UX10-X08", "the summary does not say an operator decides", VIEW, "An infrx operator decides; nothing changes until then.", "Done.", [C.v03]),
  m("UX10-X09", "no role is told why it cannot propose", VIEW, 'holds(role, "propose_publication") ? null :', "true ? null :", [C.v04, C.p02]),
  m("UX10-X10", "an administrator is told it cannot propose", VIEW, 'holds(role, "propose_publication") ? null :', "false ? null :", [C.v04, C.p01]),
  m("UX10-X11", "a stale fence does not ask for review again", VIEW, "conflict: `${REFUSAL_COPY.conflict} ${STALE_FENCE}`", "conflict: REFUSAL_COPY.conflict", [C.v05, C.p06]),
  m("UX10-X12", "?refused= is shown raw", VIEW, "export const refusalCopy = fixedCopy(REFUSALS, {", 'export const refusalCopy = (v: unknown): string | null => (typeof v === "string" ? v : null);\nexport const unused = fixedCopy(REFUSALS, {', [C.v05, C.p06]),
  m("UX10-X13", "every failed read is denied", VIEW, '("reason" in r ? r.reason === "denied" : r.error.status === 403)', "true", [C.s01, C.p04]),
  m("UX10-X14", "a 403 from the API is not denied", VIEW, "r.error.status === 403", "false", [C.s01, C.p04]),
  // the view: publication requests and readiness
  m("UX10-X15", "no recorded proof reads as can't be checked", VIEW, "r !== undefined && notFound(r.error)", "false", [C.u01]),
  m("UX10-X16", "the unmounted hosting service (LAB_HOSTING off) reads as no proof recorded", VIEW, '(e.kind === "error" || e.kind === "refusal") && e.status === 404', "e.status === 404", [C.u01]),
  m("UX10-X17", "a revision that is not ready reads ready", VIEW, "return r.data.ready\n", "return true\n", [C.u01]),
  m("UX10-X18", "not ready drops its reasons", VIEW, '${r.data.reasons.join(", ")}', "", [C.u01]),
  m("UX10-X19", "an approval without proof carries no caveat", VIEW, 'p.state === "approved" && evidence.tone !== "success" ?', "false ?", [C.u02, C.p03]),
  m("UX10-X20", "an approval with current proof still carries the caveat", VIEW, ' && evidence.tone !== "success"', "", [C.u02, C.p03]),
  m("UX10-X21", "an approval reads as published", VIEW, 'approved: { tone: "info", text: "Approved by an operator" }', 'approved: { tone: "success", text: "Published" }', [C.u02, C.p03]),
  m("UX10-X22", "the request is joined to the wrong record", VIEW, "known.get(p.deployment_revision_id)", "known.get(p.proposal_id)", [C.u02, C.p03]),
  m("UX10-X23", "any known deployment reads listed", VIEW, 'd?.visibility === "public"', "d !== undefined", [C.u02]),
  // the view: optimizations
  m("UX10-X24", "a missing base identity is not Not recorded", VIEW, 'base: v.base ? row.base : "Not recorded",', "base: row.base,", [C.o01]),
  m("UX10-X25", "a missing identity is comparable", VIEW, '? "Not comparable: an identity is not recorded"', "? null", [C.o01, C.p07]),
  m("UX10-X26", "other hardware is comparable", VIEW, ": v.base.hardware !== v.variant.hardware ?", ": false ?", [C.o02, C.p07]),
  m("UX10-X27", "a variant that is not comparable keeps its claim", VIEW, 'claim: gap === null ? row.claim : "no optimization claimed",', "claim: row.claim,", [C.o01, C.o02, C.p07]),
  m("UX10-X28", "a ratio is shown for a variant that is not comparable", VIEW, "performance: gap !== null && v.comparison?.performance ? gap : row.performance,", "performance: row.performance,", [C.o01, C.o02]),
  m("UX10-X29", "nothing measured reads as a comparison gap", VIEW, "gap !== null && v.comparison?.performance ?", "gap !== null ?", [C.o02]),
  m("UX10-X30", "the variant replaces its base invisibly", VIEW, "is a separate serving version; ${v.baseServingRef} is unchanged; it is replaced only through a release.", "replaces ${v.baseServingRef}.", [C.o03, C.p07]),
  m("UX10-X31", "the report digest is dropped", VIEW, 'digest: v.comparison?.reportDigest ?? "No comparison report",', 'digest: "No comparison report",', [C.o03, C.p07]),
  // the port
  m("UX10-X32", "the reads do not name the workspace", PORT, "const query = { provider_org_id: actor.providerId };", "const query = {};", [C.u03]),
  m("UX10-X33", "one readiness read per request, not per revision", PORT, "[...new Set(proposals.data.data.map((p) => p.deployment_revision_id))]", "proposals.data.data.map((p) => p.deployment_revision_id)", [C.u03]),
  m("UX10-X34", "an unconfigured API reads as no requests", PORT, "if (api === null) return { proposals: UNCONFIGURED,", 'if (api === null) return { proposals: { ok: true, status: 200, data: { data: [] }, requestId: "", location: null },', [C.u03]),
  m("UX10-X35", "readiness is read from the deployment record route", PORT, '"/lab/v1/control/deployments/{deployment_id}/readiness"', '"/lab/v1/control/deployments/{deployment_id}"', [C.u03]),
  // the page
  m("UX10-X40", "the form carries the policy version, not the loaded fence", PAGE, '<input type="hidden" name="fence" value={r.fence} />', '<input type="hidden" name="fence" value={r.version} />', [C.p01]),
  m("UX10-X41", "the fence is an editable field", PAGE, '<input type="hidden" name="fence" value={r.fence} />', '<input name="fence" defaultValue={r.fence} />', [C.p01]),
  m("UX10-X42", "the role note is not shown", PAGE, "{note && <p className={s.muted}>{note}</p>}", "", [C.p02]),
  m("UX10-X43", "the button is not described by its summary", PAGE, " aria-describedby={`${p.kind}-${r.fence}-${r.id}`}", "", [C.p01]),
  m("UX10-X44", "the summary sits inside the form (lab-e2e picks forms by their text)", PAGE, '<p id={`${p.kind}-${r.fence}-${r.id}`} className="lab-id">{p.summary}</p>\n                        <form action={proposeRelease}>', '<form action={proposeRelease}>\n                        <p id={`${p.kind}-${r.fence}-${r.id}`} className="lab-id">{p.summary}</p>', [C.p01]),
  m("UX10-X45", "the proof badge is success whatever was observed", PAGE, "<Badge tone={r.proof.tone}>{r.proof.label}</Badge>", '<Badge tone="success">{r.proof.label}</Badge>', [C.p02]),
  m("UX10-X46", "the proof badge carries the whole sentence", PAGE, "<Badge tone={r.proof.tone}>{r.proof.label}</Badge>", "<Badge tone={r.proof.tone}>{r.proof.text}</Badge>", [C.p01, C.l01]),
  m("UX10-X47", "a failed release read hides the publication requests", PAGE, "const requests = publicationRows(publication);", "const requests = records.ok ? publicationRows(publication) : [];", [C.p04]),
  m("UX10-X48", "unreadable releases read as none", PAGE, '<ServiceState state="unavailable" title="We couldn\'t load releases"', '<ServiceState state="empty" title="We couldn\'t load releases"', [C.p04]),
  m("UX10-X49", "refused releases read as unavailable", PAGE, 'failure(records) === "denied"', "false", [C.p04]),
  m("UX10-X50", "refused publication requests read as unavailable", PAGE, 'failure(publication.proposals) === "denied"', "false", [C.p04]),
  m("UX10-X51", "unreadable publication requests read as none", PAGE, "{!publication.proposals.ok ? (", "{false ? (", [C.p04]),
  m("UX10-X52", "a request's serving proof is not stated", PAGE, "<dt>Serving proof</dt><dd>{p.proof.text}</dd>", "", [C.p03]),
  m("UX10-X53", "the approval caveat is not rendered", PAGE, "{p.caveat && <p className={s.muted}>{p.caveat}</p>}", "", [C.p03]),
  m("UX10-X54", "the preview label shows when the stand-in is off", PAGE, '{isPreview() && <PreviewNote records="release"', '{<PreviewNote records="release"', [C.p06]),
  m("UX10-X55", "?refused= reaches the page raw", PAGE, "const refused = refusalCopy((await searchParams).refused);", 'const refused = String((await searchParams).refused ?? "") || null;', [C.p06]),
  m("UX10-X56", "optimizations are not linked from Releases", PAGE, 'href="/optimizations"', 'href="/releases"', [C.p01]),
  m("UX10-X57", "the evidence is not the release's detail", PAGE, "<summary>Evidence</summary>", "<p>Evidence</p>", [C.p01]),
  m("UX10-X58", "a release section carries a class (lab-e2e finds the exact tag)", PAGE, "<section key={r.id} aria-label={r.id}>", "<section key={r.id} aria-label={r.id} className={s.record}>", [C.p01]),
  m("UX10-X59", "the observed p99 is not told apart from the configured limit", PAGE, "Candidate p99 (observed)", "Candidate p99", [C.p01]),
  m("UX10-X60", "a failed read drops the page heading", PAGE, '<PageHeader title="Releases" purpose={PURPOSE} actions={<Link href="/optimizations" className={buttonClass()}>Optimization comparisons</Link>} />', '{records.ok && <PageHeader title="Releases" purpose={PURPOSE} actions={<Link href="/optimizations" className={buttonClass()}>Optimization comparisons</Link>} />}', [C.p04]),
  m("UX10-X61", "nothing pending reads other than R4's dash (lab-e2e E2E-R)", PAGE, '{r.pending ?? "—"}', '{r.pending ?? "None"}', [C.p01]),
  m("UX10-X62", "the summary does not wrap", PAGE, '<p id={`${p.kind}-${r.fence}-${r.id}`} className="lab-id">', "<p id={`${p.kind}-${r.fence}-${r.id}`}>", [C.l01]),
  // the loading state
  m("UX10-X63", "loading drops the heading", LOADING, "<PageHeader title=\"Releases\" purpose={PURPOSE} />", "", [C.p05]),
  m("UX10-X64", "loading reads as empty", LOADING, 'state="loading"', 'state="empty"', [C.p05]),
  // the optimization list
  m("UX10-X65", "refused variants read as unavailable", VARIANTS, 'failure(result) === "denied"', "false", [C.p07]),
  m("UX10-X66", "unreadable variants read as none", VARIANTS, '<ServiceState state="unavailable" title="We couldn\'t load optimizations"', '<ServiceState state="empty" title="We couldn\'t load optimizations"', [C.p07]),
  m("UX10-X67", "no variants loses lab-e2e's copy", VARIANTS, 'explanation="No optimized variants registered yet."', 'explanation="Nothing here."', [C.p07]),
  m("UX10-X68", "the scope gap is not the variant's badge", VARIANTS, "{v.comparable ?? v.outcome}", "{v.outcome}", [C.p07]),
  m("UX10-X69", "the report digest is not shown", VARIANTS, '<dt>Report</dt><dd><code className="lab-id">{v.digest}</code></dd>', "", [C.p07]),
  m("UX10-X70", "the serving version line is not shown", VARIANTS, '<dt>Serving version</dt><dd className="lab-id">{v.serving}</dd>', "", [C.p07]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "UX10", mutants: MUTANTS }));
