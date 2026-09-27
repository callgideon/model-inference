#!/usr/bin/env node
// L1's mutant runner (R32; LANE-RULES addendum): every L1 decision is one edit that a case it names
// must fail by assertion. A stale `find`, a load failure or a failure in an undeclared case is not a
// kill. Pattern and judging follow apps/app/tests/u/run-mutants.mjs.
// Usage: node tests/l/shell/run-mutants.mjs [--only ID,ID]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const lab = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const SUITE = ["access", "config", "request", "guard", "boundary"].map((f) => `tests/l/shell/${f}.test.ts`);

const ACCESS = "lib/auth/access.ts";
const MEMBERS = "lib/auth/memberships.ts";
const CONFIG = "lib/auth/config.ts";
const REQUEST = "lib/auth/request.ts";
const GUARD = "lib/auth/guard.ts";
const ACTION = "lib/auth/actions.ts";
const LAYOUT = "app/(provider)/layout.tsx";
const NEXT = "next.config.ts";

const C = {
  a01: "L1-A01 a signed-in user with no provider membership (consumer-only) is denied",
  a02: "L1-A02 a single membership is selected without asking",
  a03: "L1-A03 several memberships and no valid choice ask the user to select",
  a04: "L1-A04 a cookie naming a provider the user is not a member of selects nothing",
  a05: "L1-A05 a failed membership read is unavailable, never denied and never a workspace",
  a06: "L1-A06 a signed-out request is signed-out and never reads memberships",
  a07: "L1-A07 the selection action accepts only one of the user's own workspaces",
  a08: "L1-A08 the denial copy keeps consumer onboarding in the App: no signup, credits or onboarding offer",
  a09: "L1-A09 a page needs a selected workspace; the selection action needs a provider session",
  m01: "L1-M01 the membership read is the named RPC over the user's own session, with no identity argument",
  m02: "L1-M02 an RPC error or a thrown transport is unavailable",
  m03: "L1-M03 a malformed, unknown-role or duplicate row fails the whole read closed",
  c01: "L1-C01 the origin is explicit: development defaults to port 3100, production needs its own https origin",
  c02: "L1-C02 cookies are secure exactly when the origin is https",
  c03: "L1-C03 the Lab session cookie is its own and host-only: never the App's name, never a domain",
  c04: "L1-C04 the workspace preference cookie is http-only and bounded",
  c05: "L1-C05 every Lab response is private and no-store, and does not advertise the framework",
  r01: "L1-R01 a misconfigured Lab is unavailable and builds no client",
  r02: "L1-R02 the session client is the Lab's: its cookie options, its env, and the request's cookie store",
  r03: "L1-R03 the user comes from the session and the workspace from the Lab's cookie, re-checked against memberships",
  g01: "L1-G01 a page gets a workspace only when one is selected: the picker state is a 404, never the first workspace",
  g02: "L1-G02 the selection action runs for any provider session and is a 404 for everyone else",
  g03: "L1-G03 the guard reads through the Lab's own session client and sends no identity",
  b01: "L1-B01 every page, route, provider layout and server action calls the provider guard",
  b02: "L1-B02 the provider layout renders its children only for a ready workspace",
  b03: "L1-B03 the selection action stores the membership it validated, never the submitted value",
  b04: "L1-B04 the Lab never imports the App: shared code comes only from packages/shared",
};

const m = (id, what, file, find, replace, cases) => ({ id, what, file, find, replace, cases });
const MUTANTS = [
  m("L1-X01", "a consumer-only user gets the workspace picker", ACCESS, '  if (workspaces.length === 0) return { kind: "denied" };\n', "", [C.a01]),
  m("L1-X02", "an unmatched cookie falls back to the first workspace", ACCESS, "workspaces.find((m) => m.providerId === selected);", "(workspaces.find((m) => m.providerId === selected) ?? workspaces[0]);", [C.a03, C.a04]),
  m("L1-X03", "a single membership is not auto-selected", ACCESS, "workspaces.length === 1 ? workspaces[0] : ", "", [C.a02, C.a04]),
  m("L1-X04", "a failed read reads as denied", ACCESS, 'if (!read.ok) return { kind: "unavailable" };', 'if (!read.ok) return { kind: "denied" };', [C.a05]),
  m("L1-X05", "memberships are read before the session is checked", ACCESS,
    '  if ((await deps.userId()) === null) return { kind: "signed-out" };\n  return providerAccess(await deps.memberships(), deps.selected);',
    '  const read = await deps.memberships();\n  if ((await deps.userId()) === null) return { kind: "signed-out" };\n  return providerAccess(read, deps.selected);', [C.a06]),
  m("L1-X06", "a signed-out request is treated as signed in", ACCESS, '  if ((await deps.userId()) === null) return { kind: "signed-out" };\n', "", [C.a06, C.r03]),
  m("L1-X07", "the selection action falls back to any workspace", ACCESS, "find((m) => m.providerId === requested) ?? null;", "find((m) => m.providerId === requested) ?? sessionAccess(access)?.workspaces[0] ?? null;", [C.a07]),
  m("L1-X08", "the denial offers the consumer signup grant", ACCESS, "Consumer accounts use the infrx App.", "Sign up for 10,000 free credits.", [C.a08]),
  m("L1-X09", "a picker state counts as a selected workspace", ACCESS, 'access.kind === "ready" ? access.workspace : null', 'access.kind === "ready" ? access.workspace : access.kind === "select" ? access.workspaces[0] : null', [C.a09]),
  m("L1-X10", "a denied user counts as a provider session", ACCESS, 'access.kind === "select" || access.kind === "ready" ? access : null', 'access.kind === "unavailable" ? null : (access as SessionAccess)', [C.a09, C.a07]),
  m("L1-X11", "the membership read sends a client-side identity", MEMBERS, "client.rpc(MEMBERSHIPS_RPC)", 'client.rpc(MEMBERSHIPS_RPC, { user_id: "" })', [C.m01]),
  m("L1-X12", "an RPC error reads as no memberships", MEMBERS, "error ? { ok: false } :", "error ? { ok: true, memberships: [] } :", [C.m02]),
  m("L1-X13", "a transport failure reads as no memberships", MEMBERS, "  } catch {\n    return { ok: false };", "  } catch {\n    return { ok: true, memberships: [] };", [C.m02]),
  m("L1-X14", "an unknown role is accepted", MEMBERS, "    if (!ROLES.includes(role as Role)) return { ok: false };\n", "", [C.m03]),
  m("L1-X15", "a malformed row is skipped instead of failing the read", MEMBERS, "if (typeof id !== \"string\" || !UUID.test(id)) return { ok: false };", "if (typeof id !== \"string\" || !UUID.test(id)) continue;", [C.m03]),
  m("L1-X16", "a duplicate provider row is accepted", MEMBERS, "    if (memberships.some((m) => m.providerId === id)) return { ok: false };\n", "", [C.m03]),
  m("L1-X17", "an empty provider name is accepted", MEMBERS, 'typeof name !== "string" || name === ""', 'typeof name !== "string"', [C.m03]),
  m("L1-X18", "the RPC name drifts from the one membership read (R156)", MEMBERS, '"lab_provider_memberships"', '"lab_my_provider_memberships"', [C.m01]),
  m("L1-X20", "production accepts an http origin", CONFIG, '  if (production && url.protocol !== "https:") return null;\n', "", [C.c01]),
  m("L1-X21", "an origin with a path is accepted", CONFIG, '  if (url.origin !== raw.replace(/\\/$/, "")) return null; // an origin, no path\n', "", [C.c01]),
  m("L1-X19", "a missing Supabase URL is not a misconfiguration", CONFIG, "!supabaseUrl || !anonKey", "!anonKey", [C.c01]),
  m("L1-X22", "a missing anon key is not a misconfiguration", CONFIG, "!supabaseUrl || !anonKey", "!supabaseUrl", [C.c01]),
  m("L1-X41", "an unparsable origin throws out of the guard", CONFIG, "  let url: URL;\n  try {\n    url = new URL(raw);\n  } catch {\n    return null;\n  }\n", "  const url = new URL(raw);\n", [C.c01]),
  m("L1-X23", "cookies are never secure", CONFIG, 'secure: url.protocol === "https:"', "secure: false", [C.c02]),
  m("L1-X24", "the session cookie is widened to a parent domain", CONFIG, 'return { name: AUTH_COOKIE, path: "/",', 'return { name: AUTH_COOKIE, domain: ".callbill.ai", path: "/",', [C.c03]),
  m("L1-X25", "the workspace cookie is widened to a parent domain", CONFIG, 'return { httpOnly: true, path: "/",', 'return { httpOnly: true, domain: ".callbill.ai", path: "/",', [C.c03]),
  m("L1-X26", "the Lab rides on the App's session cookie name", CONFIG, '"sb-infrx-lab-auth"', '"sb-fcbnscgsymzdykendbrc-auth-token"', [C.c03]),
  m("L1-X27", "the workspace cookie is readable by scripts", CONFIG, "return { httpOnly: true,", "return { httpOnly: false,", [C.c04]),
  m("L1-X28", "the workspace cookie never expires", CONFIG, ", maxAge: 60 * 60 * 24 * 30 }", " }", [C.c04]),
  m("L1-X29", "Lab responses become cacheable", NEXT, '"private, no-store"', '"public, max-age=60"', [C.c05]),
  m("L1-X30", "the framework header is advertised", NEXT, "poweredByHeader: false", "poweredByHeader: true", [C.c05]),
  m("L1-X31", "a misconfigured Lab still builds a client", REQUEST, '  if (config === null) return { kind: "unavailable" };\n', "  if (config === null) return resolveAccess({ userId: async () => null, memberships: async () => ({ ok: false }), selected: undefined });\n  makeClient(\"\", \"\", {} as ClientOptions);\n", [C.r01]),
  m("L1-X32", "the session client drops the Lab cookie options", REQUEST, "    cookieOptions: authCookieOptions(config),\n", "    cookieOptions: { ...authCookieOptions(config), name: undefined },\n", [C.r02]),
  m("L1-X33", "refreshed session cookies are dropped", REQUEST, "for (const { name, value, options } of list) store.set(name, value, options);", "void list;", [C.r02]),
  m("L1-X34", "a read-only cookie store throws out of setAll", REQUEST, "        } catch {\n", "        } catch (error) {\n          throw error;\n", [C.r02]),
  m("L1-X35", "the workspace preference is ignored", REQUEST, "selected: store.get(WORKSPACE_COOKIE)?.value,", "selected: undefined,", [C.r03]),
  m("L1-X36", "the selection action skips the provider guard", ACTION, "const access = await requireProviderSession();", 'const access = { kind: "denied" } as const;', [C.b01]),
  m("L1-X37", "the picker state renders the page", LAYOUT, "        <Workspaces workspaces={access.workspaces} />\n      </main>", "        <Workspaces workspaces={access.workspaces} />\n        {children}\n      </main>", [C.b02]),
  m("L1-X38", "the provider layout is prerendered", LAYOUT, 'export const dynamic = "force-dynamic";', "", [C.b02]),
  m("L1-X39", "the action stores the submitted value", ACTION, ".set(WORKSPACE_COOKIE, chosen.providerId,", '.set(WORKSPACE_COOKIE, String(formData.get("providerId")),', [C.b03]),
  // Fix round (0-L1-R-2): guard.ts is run by guard.test.ts; X42 is the reviewer's type-correct H2'.
  m("L1-X42", "a page under the picker state gets the first workspace (H2')", GUARD, "const workspace = readyWorkspace(await providerAccessForRequest());",
    "const access = await providerAccessForRequest();\n  const workspace = readyWorkspace(access) ?? sessionAccess(access)?.workspaces[0] ?? null;", [C.g01]),
  m("L1-X43", "the page guard renders without a workspace", GUARD, "  if (workspace === null) notFound();\n", "", [C.g01]),
  m("L1-X44", "the action guard runs without a provider session", GUARD, "  if (access === null) notFound();\n", "", [C.g02]),
  m("L1-X45", "the action guard admits any signed-in state", GUARD, "const access = sessionAccess(await providerAccessForRequest());", "const access = (await providerAccessForRequest()) as SessionAccess;", [C.g02]),
  m("L1-X46", "the guard's client drops the Lab cookie options", GUARD, "createServerClient(url, key, options)", "createServerClient(url, key, { cookies: options.cookies })", [C.g03]),
  m("L1-X47", "the guard's membership read sends an identity", GUARD, "rpc: (name) => client.rpc(name)", 'rpc: (name) => client.rpc(name, { user_id: "" })', [C.g03]),
  // Fix round (0-L1-R-1): server actions in every form Next accepts; X48 is the reviewer's H1.
  m("L1-X48", "an unguarded arrow-const server action (H1)", ACTION, '  redirect("/");\n}\n',
    '  redirect("/");\n}\n\nexport const peekWorkspaces = async (formData: FormData) => {\n  return String(formData.get("providerId"));\n};\n', [C.b01]),
  m("L1-X49", "an unguarded default-export server action", ACTION, '  redirect("/");\n}\n', '  redirect("/");\n}\n\nexport default async function peek() {\n  return 1;\n}\n', [C.b01]),
  m("L1-X50", "an unguarded inline server action in the provider layout", LAYOUT, "<form action={selectWorkspace}>", '<form action={async () => { "use server"; }}>', [C.b01]),
  m("L1-X40", "the Lab imports the App's code", ACCESS, "export const ROLES", 'import type {} from "../../../app/lib/types.ts";\nexport const ROLES', [C.b04]),
];

function copy() {
  const root = mkdtempSync(join(tmpdir(), "l1-mutants-"));
  cpSync(lab, root, { recursive: true, filter: (s) => !/(node_modules|\.next)(\/|$)/.test(s.slice(lab.length)) });
  symlinkSync(join(lab, "node_modules"), join(root, "node_modules"), "dir");
  return root;
}

function run(cwd) {
  return new Promise((done) => {
    const child = spawn(process.execPath, ["--test", "--test-reporter=tap", ...SUITE], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (c) => (out += c));
    child.stderr.on("data", (c) => (out += c));
    child.on("close", (code) => done({ code, out }));
  });
}

function failed(out) {
  const cases = [];
  const re = /^ *not ok \d+ - (.*)$/gm;
  for (let hit = re.exec(out); hit !== null; hit = re.exec(out)) {
    const rest = out.slice(hit.index + hit[0].length);
    const end = rest.search(/^ *\.\.\.$/m);
    cases.push({ name: hit[1].trim(), assertion: /code: 'ERR_ASSERTION'/.test(end === -1 ? rest : rest.slice(0, end)) });
  }
  return cases;
}

async function judge(mutant) {
  const pristine = readFileSync(join(lab, mutant.file), "utf8");
  const hits = pristine.split(mutant.find).length - 1;
  if (hits !== 1) return `STALE (find matches ${hits} times)`;
  const root = copy();
  try {
    writeFileSync(join(root, mutant.file), pristine.replace(mutant.find, mutant.replace));
    const { code, out } = await run(root);
    if (code === 0) return "SURVIVED (suite passed)";
    const fails = failed(out);
    if (fails.some((f) => /\.test\.ts$/.test(f.name))) return "RUNNER-ERROR (a test file did not load)";
    const hit = fails.find((f) => f.assertion && mutant.cases.includes(f.name));
    return hit ? `killed by "${hit.name}"` : `SURVIVED (failed only: ${fails.map((f) => f.name).join("; ")})`;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

// Every case in the suite is named by at least one mutant, and every named case exists.
const declared = new Set(MUTANTS.flatMap((x) => x.cases));
const baseline = await run(lab);
const cases = [...baseline.out.matchAll(/^ *ok \d+ - (L1-\S+ .*)$/gm)].map((x) => x[1].trim());
const problems = [
  ...(baseline.code === 0 ? [] : ["the unmutated suite does not pass"]),
  ...cases.filter((name) => !declared.has(name)).map((name) => `no mutant names "${name}"`),
  ...[...declared].filter((name) => !cases.includes(name)).map((name) => `a mutant names a missing case "${name}"`),
];
const selected = only === null ? MUTANTS : MUTANTS.filter((x) => only.includes(x.id));
if (selected.length === 0) problems.push("--only matched no mutant");
for (const problem of problems) console.log(`FAIL ${problem}`);
const bad = problems.length;
let survivors = 0;
for (const mutant of selected) {
  const verdict = await judge(mutant);
  if (!verdict.startsWith("killed")) survivors += 1;
  console.log(`${verdict.startsWith("killed") ? "killed " : "NOT KILLED"} ${mutant.id} ${mutant.what} — ${verdict}`);
}
console.log(`\n${cases.length} cases, all named: ${bad === 0}; ${selected.length} mutants, ${selected.length - survivors} killed, ${survivors} not killed`);
process.exit(bad === 0 && survivors === 0 ? 0 : 1);
