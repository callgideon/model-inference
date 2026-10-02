#!/usr/bin/env node
// UX lane mutant runner (R32; UX-00/01/02): every decision the UXA (foundations), UXR (UX-T01 truthful
// retention), UXN (UX-T02 mobile navigation) and UXM (UX-11 matrix runner) cases claim is one edit that a case it names must fail
// by ASSERTION. A stale `find`, a copy that fails to load, a failure only by exception or only in an
// undeclared case is not a kill; every UXA/UXR/UXN case must be named by a mutant.
//
// The copy is <tmp>/apps/app (node_modules linked) beside a link to apps/infrx-api, whose published
// fixtures the retention cases read. The UXN cases serve the copy's own synthetic harness (next dev +
// Chromium), so a full run takes a few minutes.
// Usage: node tests/ux/run-mutants.mjs [--only ID,ID]
import { spawn } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const app = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const m = (id, what, file, find, replace, cases, suite = SUITE) => ({ id, what, file, find, replace, cases, suite });

const SUITE = ["tests/ux/foundations.test.ts", "tests/ux/retention.test.ts", "tests/ux/shell/navigation.test.ts"];
// The UXM cases are pure: their mutants run only the matrix suite (no harness server per mutant).
const MATRIX = ["tests/ux/matrix/matrix.test.ts"];
const PREFIX = /^UX[ARNM]-\S+ /;
const RUNNER = "tests/ux/matrix/run-matrix.mjs";

const CONTENT = "app/(console)/docs/content.ts";
const MODEL = "lib/contracts/v2/published-model.ts";
const PANEL = "app/(console)/usage/[requestId]/result-panel.tsx";
const NAV = "components/sidebar.tsx";

const C = {
  a01: "UXA-S01 success and warning are semantic tokens in both themes and Tailwind colours, beside the existing ones",
  a02: "UXA-S02 no production module reaches the synthetic harness or its fixtures",
  r01: "UXR-01 the live null bound never renders a deletion deadline: no 0 hours, the unknown is said",
  r02: "UXR-02 an absent bound reads the same as null (Python's `Count | None = None`)",
  r03: "UXR-03 a committed positive bound is stated, and only then",
  r04: "UXR-04 the other lifetimes stay the record's with an unknown bound: 24 h result, 1 h stream, 24 h key, 7 d cache",
  r05: "UXR-05 a zero or malformed bound fails safely: the catalog is unavailable, never a figure",
  r06: "UXR-06 the copy guard covers the unknown sentence: every renderable sentence for a live-shaped record",
  r07: "UXR-07 an expired result says it is no longer available, never that its content was removed or deleted",
  n01: "UXN-01 at 390px with the menu closed, Tab never reaches an offscreen or hidden link",
  n02: "UXN-02 the open menu is a modal: focus moves in, Tab cannot reach the page, Escape closes and returns focus",
  n03: "UXN-03 a route change closes the menu: following its link (the current page's too), and going back",
  n04: "UXN-04 growing to desktop closes the open menu and leaves the persistent navigation usable",
  n05: "UXN-05 desktop: a skip link first, the nav in the agreed order, aria-current on the page, Docs internal",
  n06: "UXN-06 a long email, an unreadable balance and the operator entry fit the menu and the sidebar without overflow",
  m01: "UXM-01 TAP: ok, not ok, SKIP and TODO are read; an escaped hash-SKIP inside a name is not a directive",
  m02: "UXM-02 a declared-blocked part is BLOCKED with its cause and the lane that unblocks it, whatever a run says",
  m03: "UXM-03 a part whose suite has not merged is BLOCKED naming the lane and the missing path",
  m04: "UXM-04 a skip is never a pass: no case, or only skipped cases, is BLOCKED",
  m05: "UXM-05 a failed case, or a suite that exits non-zero, is FAIL",
  m06: "UXM-06 a part's match selects its cases: unmatched cases neither pass nor block it",
  m07: "UXM-07 the verdict ranks FAIL > INVALID > BLOCKED > PASS; a dirty tree is INVALID; a dangling fixture fails it",
  m08: "UXM-08 a fixture reference is COVERED only when its file exists and holds its symbol; a gap stays a GAP",
  m09: "UXM-09 the matrix declares T01..T13 once each; every part names a lane and either suite paths or its blocking cause",
};

const MUTANTS = [
  m("UXA-X01", "the dark theme loses its success colour", "app/globals.css", "  --success: oklch(0.723 0.219 149.579);\n", "", [C.a01]),
  m("UXA-X02", "a production component imports the harness", "components/page-header.tsx", "export function PageHeader({", 'import type { Harness } from "../tests/ux/browser";\nexport type Unused = Harness;\nexport function PageHeader({', [C.a02]),
  m("UXR-X01", "the live null reaches duration() again (the audited bug)", CONTENT, "r.physical_deletion_bound_s == null", "r.physical_deletion_bound_s === undefined", [C.r01, C.r06]),
  m("UXR-X02", "an unknown bound is silently omitted", CONTENT, "      ? NO_DELETION_DEADLINE\n", '      ? ""\n', [C.r01, C.r02, C.r06]),
  m("UXR-X03", "a committed bound is hidden as unknown", CONTENT, "      : `Expired content is physically deleted within ${duration(r.physical_deletion_bound_s)}.`,", "      : NO_DELETION_DEADLINE,", [C.r03]),
  m("UXR-X04", "the stream lifetime reads the result's", CONTENT, "Last-Event-ID for ${duration(r.stream_journal_ttl_s)}", "Last-Event-ID for ${duration(r.result_ttl_s)}", [C.r04]),
  m("UXR-X05", "the copy guard skips the retention sentences", CONTENT, "    ...retentionFacts(model.retention),\n", "", [C.r06]),
  m("UXR-X06", "a null bound makes the whole catalog unavailable (over-correction)", MODEL, '  ["physical_deletion_bound_s"],', "  [],", [C.r01]),
  m("UXR-X07", "a zero bound is accepted", MODEL, 'v < 1) fail(p, "an integer >= 1");', 'v < 0) fail(p, "an integer >= 1");', [C.r05]),
  m("UXR-X08", "a string bound is accepted unchecked", MODEL, "    physical_deletion_bound_s: count,", "    physical_deletion_bound_s: () => {},", [C.r05]),
  m("UXN-X01", "the closed sidebar is moved offscreen instead of hidden (audit C9)", NAV, '"hidden w-60 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex"', '"fixed inset-y-0 left-0 z-50 flex w-60 -translate-x-full flex-col border-r bg-sidebar text-sidebar-foreground md:static md:translate-x-0"', [C.n01]),
  m("UXN-X02", "the menu is not modal: Tab reaches the page behind", NAV, "<Dialog.Root open={open}", "<Dialog.Root modal={false} open={open}", [C.n02]),
  m("UXN-X03", "Escape (and every dismissal) leaves the menu open (audit C9)", NAV, "onOpenChange={(next) => setOpenAt(next ? pathname : null)}", "onOpenChange={(next) => next && setOpenAt(pathname)}", [C.n02]),
  m("UXN-X04", "closing does not return focus to the menu button", NAV, '<Dialog.Popup className="fixed', '<Dialog.Popup finalFocus={false} className="fixed', [C.n02]),
  m("UXN-X05", "a back navigation leaves the menu open", NAV, "const open = openAt === pathname;", "const open = openAt !== null;", [C.n03]),
  m("UXN-X06", "the current page's link leaves the menu open", NAV, "              <Contents pathname={pathname} onNavigate={close} {...account} />", "              <Contents pathname={pathname} onNavigate={() => {}} {...account} />", [C.n03]),
  m("UXN-X07", "growing to desktop leaves the modal open", NAV, "if (desktop.matches) setOpenAt(null);", "if (false) setOpenAt(null);", [C.n04]),
  m("UXN-X08", "the skip link goes nowhere", NAV, "href={`#${MAIN_ID}`}", 'href="#"', [C.n05]),
  m("UXN-X09", "the current page is not marked", NAV, 'aria-current={active ? "page" : undefined}', "aria-current={undefined}", [C.n05]),
  m("UXN-X10", "a prefix marks every page current", NAV, "const active = pathname === href || pathname.startsWith(`${href}/`);", "const active = true;", [C.n05]),
  m("UXN-X11", "the navigation keeps the old order", NAV, '  { href: "/api-keys", label: "API keys", icon: KeyRound },\n  { href: "/usage", label: "Usage", icon: BarChart3 },', '  { href: "/usage", label: "Usage", icon: BarChart3 },\n  { href: "/api-keys", label: "API keys", icon: KeyRound },', [C.n05]),
  m("UXN-X12", "Docs wears the external-link icon again", NAV, "  BookOpen,\n", "  ExternalLink as BookOpen,\n", [C.n05]),
  m("UXN-X13", "the operator entry disappears", NAV, "{isOperator ? (", "{false ? (", [C.n06]),
  m("UXN-X14", "a long email widens the navigation", NAV, '<span className="min-w-0 flex-1 truncate">{email}</span>', '<span className="flex-1 whitespace-nowrap">{email}</span>', [C.n06]),
  m("UXN-X15", "an unreadable balance is not stated", NAV, "{balance === null ? (", "{false ? (", [C.n06]),
  m("UXM-X01", "a skipped case counts as run", RUNNER, "skipped: directive !== undefined,", "skipped: false,", [C.m01, C.m04], MATRIX),
  m("UXM-X02", "a TODO directive is read as a failure", RUNNER, "(SKIP|TODO)", "(SKIP)", [C.m01], MATRIX),
  m("UXM-X03", "only top-level TAP lines are read: nested cases vanish", RUNNER, "/^ *(not )?ok", "/^(not )?ok", [C.m01], MATRIX),
  m("UXM-X04", "a declared-blocked part runs anyway", RUNNER, "  if (part.blocked) return", "  if (false) return", [C.m02], MATRIX),
  m("UXM-X05", "the blocked cause drops the unblocking lane", RUNNER, "cause: `${part.blocked} (unblocks: ${part.lane})`", "cause: part.blocked", [C.m02], MATRIX),
  m("UXM-X06", "an unmerged suite is not named", RUNNER, "if (run.missing.length > 0) return", "if (false) return", [C.m03], MATRIX),
  m("UXM-X07", "no case run reads as PASS", RUNNER, "if (counts.passed === 0) return", "if (false) return", [C.m04], MATRIX),
  m("UXM-X08", "a non-zero exit with no failed case passes", RUNNER, "if (failed > 0 || run.code !== 0)", "if (failed > 0)", [C.m05], MATRIX),
  m("UXM-X09", "a failed case is not FAIL", RUNNER, "if (failed > 0 || run.code !== 0)", "if (run.code !== 0)", [C.m05], MATRIX),
  m("UXM-X10", "a part's match is ignored", RUNNER, "const match = part.match ? new RegExp(part.match) : null;", "const match = null;", [C.m06], MATRIX),
  m("UXM-X11", "INVALID ranks below BLOCKED", RUNNER, 'export const RANK = ["PASS", "BLOCKED", "INVALID", "FAIL"];', 'export const RANK = ["PASS", "INVALID", "BLOCKED", "FAIL"];', [C.m07], MATRIX),
  m("UXM-X12", "a dirty tree still yields a verdict for the SHA", RUNNER, '...(dirty ? ["INVALID"] : []),', "", [C.m07], MATRIX),
  m("UXM-X13", "a dangling fixture reference is ignored", RUNNER, '...(fixtures.some((f) => f.status === "DANGLING") ? ["FAIL"] : []),', "", [C.m07], MATRIX),
  m("UXM-X14", "a fixture file that exists counts without its symbol", RUNNER, ' && readFileSync(file, "utf8").includes(entry.contains)', "", [C.m08], MATRIX),
  m("UXM-X15", "a gap reads as covered", RUNNER, 'if (entry.gap) return "GAP";', 'if (entry.gap) return "COVERED";', [C.m08], MATRIX),
  m("UXM-X16", "the matrix loses a journey", "tests/ux/matrix/matrix.json", '      "id": "T13",', '      "id": "T13b",', [C.m09], MATRIX),
  m("UXR-X09", "an expired result claims its content was removed", PANEL, '"This result is no longer available. Request status and usage remain available."', "\"The result expired and its content was removed. The request's details and charge stay on this page.\"", [C.r07]),
];

/** The failed cases in a TAP report and whether each failed by assertion (Lab harness rule). */
function failed(out) {
  const cases = [];
  const re = /^( *)not ok \d+ - (.*)$/gm;
  for (let hit = re.exec(out); hit !== null; hit = re.exec(out)) {
    const rest = out.slice(hit.index + hit[0].length);
    const end = rest.search(new RegExp(`^${hit[1]}  \\.\\.\\.$`, "m"));
    cases.push({ name: hit[2].trim(), assertion: /code: 'ERR_ASSERTION'/.test(end === -1 ? rest : rest.slice(0, end)) });
  }
  return cases;
}

function tap(cwd, suite) {
  return new Promise((done) => {
    const child = spawn(process.execPath, ["--test", "--test-reporter=tap", ...suite], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (c) => (out += c));
    child.stderr.on("data", (c) => (out += c));
    child.on("close", (code) => done({ code, out }));
  });
}

async function judge(mutant) {
  const pristine = readFileSync(join(app, mutant.file), "utf8");
  const hits = pristine.split(mutant.find).length - 1;
  if (hits !== 1) return `STALE (find matches ${hits} times)`;
  const root = mkdtempSync(join(tmpdir(), "app-ux-mutants-"));
  try {
    const copy = join(root, "apps/app");
    mkdirSync(join(root, "apps"), { recursive: true });
    cpSync(app, copy, { recursive: true, filter: (s) => !/(node_modules|\.next)(\/|$)/.test(s.slice(app.length)) });
    symlinkSync(join(app, "node_modules"), join(copy, "node_modules"), "dir");
    symlinkSync(resolve(app, "../infrx-api"), join(root, "apps/infrx-api"), "dir");
    writeFileSync(join(copy, mutant.file), pristine.replace(mutant.find, () => mutant.replace));
    const { code, out } = await tap(copy, mutant.suite);
    if (code === 0) return "SURVIVED (suite passed)";
    const fails = failed(out);
    if (fails.some((f) => /\.test\.ts$/.test(f.name))) return "RUNNER-ERROR (a test file did not load)";
    const hit = fails.find((f) => f.assertion && mutant.cases.includes(f.name));
    return hit ? `killed by "${hit.name}"` : `SURVIVED (failed only: ${fails.map((f) => `${f.name}${f.assertion ? "" : " [error]"}`).join("; ")})`;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

const declared = new Set(MUTANTS.flatMap((x) => x.cases));
const baseline = await tap(app, [...SUITE, ...MATRIX]);
const passed = [...baseline.out.matchAll(/^ *ok \d+ - (.*)$/gm)].map((x) => x[1].trim());
const cases = passed.filter((name) => PREFIX.test(name));
const problems = [
  ...(baseline.code === 0 ? [] : ["the unmutated suite does not pass"]),
  ...cases.filter((name) => !declared.has(name)).map((name) => `no mutant names "${name}"`),
  ...[...declared].filter((name) => !passed.includes(name)).map((name) => `a mutant names a missing case "${name}"`),
];
const selected = only === null ? MUTANTS : MUTANTS.filter((x) => only.includes(x.id));
if (selected.length === 0) problems.push("--only matched no mutant");
for (const problem of problems) console.log(`FAIL ${problem}`);
let survivors = 0;
for (const mutant of selected) {
  const verdict = await judge(mutant);
  if (!verdict.startsWith("killed")) survivors += 1;
  console.log(`${verdict.startsWith("killed") ? "killed " : "NOT KILLED"} ${mutant.id} ${mutant.what} — ${verdict}`);
}
console.log(`\n${cases.length} cases, all named: ${problems.length === 0}; ${selected.length} mutants, ${selected.length - survivors} killed, ${survivors} not killed`);
process.exit(problems.length === 0 && survivors === 0 ? 0 : 1);
