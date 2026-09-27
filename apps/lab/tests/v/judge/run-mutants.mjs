#!/usr/bin/env node
// V3's mutant runner (R32; LANE-RULES addendum) over tests/v/judge, on the shared Lab harness.
// Usage: node tests/v/judge/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["view", "panel"].map((f) => `tests/v/judge/${f}.test.ts`);
const PORT = "components/traces/judge/port.ts";
const FAKE = "components/traces/judge/fake.ts";
const VIEW = "components/traces/judge/view.ts";
const PANEL = "components/traces/judge/panel.tsx";
const PAGE = "app/(provider)/requests/[id]/page.tsx";

const C = {
  j01: "V3-J01 a dry run is labelled an estimate: no scores, no verdict, nothing reserved",
  j02: "V3-J02 a collected live run shows each score out of its maximum with the judge model and rubric",
  j03: "V3-J03 without media, media criteria read not scored and the verdict is never a pass",
  j04: "V3-J04 in-flight and ambiguous runs show the held USD budget and no scores; nothing is ever CREDIT",
  j05: "V3-J05 calibration is claimed only with the statistics and enough operator labels behind it",
  j06: "V3-J06 the judge read is the provider's own, developer+, and unavailable until the real adapter is wired",
  p01: "V3-P01 the judge panel is read only, shows refusals as fixed copy and links judge consent to Settings",
  p02: "V3-P02 the page reads judge runs as the session's actor and shows them only beside a visible request",
};

const MUTANTS = [
  m("V3-X01", "a dry run shows scores", VIEW, 'const scored = !dry && r.state === "collected";', 'const scored = dry || r.state === "collected";', [C.j01]),
  m("V3-X02", "a dry run is labelled as a live result", VIEW, "label: dry ? JUDGE_COPY.dry_run : ", "label: ", [C.j01]),
  m("V3-X03", "a dry run has no dry-run verdict", VIEW, "verdict: dry ? JUDGE_COPY.no_verdict_dry_run : ", "verdict: ", [C.j01]),
  m("V3-X04", "a dry run claims a reservation", VIEW, "nothing reserved, nothing sent.", "reserved.", [C.j01]),
  m("V3-X05", "a score loses its maximum", VIEW, "`${s.score}/${s.max}`", "`${s.score}`", [C.j02]),
  m("V3-X06", "the label loses the rubric version", VIEW, " · rubric v${r.rubricVersion}`", "`", [C.j02]),
  m("V3-X07", "a failed run reads as pass", VIEW, 'r.overallPass ? "pass" : "fail"', '"pass"', [C.j02]),
  m("V3-X08", "no overall verdict reads as fail", VIEW, " || r.overallPass === null", "", [C.j02]),
  m("V3-X09", "a missing score renders as a number", VIEW, 's.score === null ? "not scored" : ', "", [C.j02]),
  m("V3-X10", "the charge shown is the hold, not the actual", VIEW, "`Collected; ${r.actualUsd} USD charged.`", "`Collected; ${r.reservedUsd} USD charged.`", [C.j02]),
  m("V3-X11", "a media criterion is scored from text", VIEW, "s.requiresMedia && !r.media ? JUDGE_COPY.no_media : ", "", [C.j03]),
  m("V3-X12", "a no-media run can pass", VIEW, "blind ? JUDGE_COPY.no_media_verdict : ", "", [C.j03]),
  m("V3-X13", "a text-only rubric cannot pass without media", VIEW, "const blind = !r.media && r.scores.some((s) => s.requiresMedia);", "const blind = !r.media;", [C.j03]),
  m("V3-X14", "an ambiguous submit hides its held budget", VIEW, '  if (r.state === "ambiguous") return `Submit outcome unknown: ${r.reservedUsd} USD held until reconciled; it is never resubmitted.`;\n', "", [C.j04]),
  m("V3-X15", "an uncollected run shows scores", VIEW, 'const scored = !dry && r.state === "collected";', 'const scored = !dry && r.state !== "released";', [C.j04]),
  m("V3-X16", "a released run reads as held", VIEW, '  if (r.state === "released") return "Released; nothing charged.";\n', "", [C.j04]),
  m("V3-X17", "an in-flight run hides its hold", VIEW, "`Awaiting the judge: ${r.reservedUsd} USD held.`", '"Awaiting the judge."', [C.j04]),
  m("V3-X18", "a USD estimate is labelled CREDIT", VIEW, "`Estimated worst case ${r.reservedUsd} USD;", "`Estimated worst case ${r.reservedUsd} CREDIT;", [C.j01, C.j04]),
  m("V3-X19", "calibrated with too few operator labels", VIEW, " && c.labels >= c.required", "", [C.j05]),
  m("V3-X20", "calibrated without an agreement statistic", VIEW, " && c.agreement !== null", "", [C.j05]),
  m("V3-X21", "any state with statistics reads as calibrated", VIEW, 'if (c.state === "calibrated" && ', "if (", [C.j05]),
  m("V3-X22", "no labels reads as a count, not uncalibrated", VIEW, "  if (c.labels === 0) return JUDGE_COPY.uncalibrated;\n", "", [C.j05]),
  m("V3-X23", "a row claims calibration on its own", VIEW, "calibration: calibrationCopy(r.calibration),", 'calibration: "Calibrated.",', [C.j05]),
  m("V3-X24", "agreement is not shown as a percentage", VIEW, "const pct = (x: number) => `${Math.round(x * 100)}%`;", "const pct = (x: number) => `${x}`;", [C.j05]),
  m("V3-X25", "another provider's runs are returned", FAKE, "r.providerId === actor.providerId && ", "", [C.j06]),
  m("V3-X26", "another request's runs are returned", FAKE, " && r.requestId === requestId", "", [C.j06]),
  m("V3-X27", "a viewer reads judge results", FAKE, '    if (actor.role === "viewer") return { ok: false, reason: "denied" };\n', "", [C.j06]),
  m("V3-X28", "the default judge port answers", PORT, "  return UNAVAILABLE;\n}", "  return { runs: async () => ({ ok: true, value: [] }) };\n}", [C.j06]),
  m("V3-X29", "the panel offers a paid run", PANEL, "      <p>\n        Judge consent", '      <form action="/judge"><button type="submit">Run judge</button></form>\n      <p>\n        Judge consent', [C.p01]),
  m("V3-X30", "a refusal is shown raw", PANEL, "{JUDGE_COPY[result.reason]}", "{result.reason}", [C.p01]),
  m("V3-X31", "consent is not linked to Settings", PANEL, '<Link href="/settings">Settings</Link>', "Settings", [C.p01]),
  m("V3-X32", "the panel hides dry runs", PANEL, "judgeRows(result.value)", 'judgeRows(result.value.filter((r) => r.mode === "live"))', [C.p01]),
  m("V3-X35", "the panel prints Calibrated. for every run", PANEL, "<p>{r.calibration}</p>", "<p>Calibrated.</p>", [C.p01]),
  m("V3-X36", "the panel prints pass for every run", PANEL, "<p>Verdict: {r.verdict}</p>", "<p>Verdict: pass</p>", [C.p01]),
  m("V3-X33", "judge runs show beside a missing request", PAGE, "{trace.ok && <JudgePanel result={judge} />}", "<JudgePanel result={judge} />", [C.p02]),
  m("V3-X34", "judge runs are read as a provider from the URL", PAGE, "judgePort().runs(actor, id)", 'judgePort().runs({ providerId: id, role: "administrator" }, id)', [C.p02]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "V3", mutants: MUTANTS }));
