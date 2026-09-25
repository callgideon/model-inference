// node --test "tests/**/*.test.ts"
//
// A2, the static half: what ships in which bundle, and what the auth pages are allowed to say.
// These read source, because the properties are about the bundle and the copy, not about a run.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { CLAIM_RPC, claimArgs } from "../../app/(auth)/flow.ts";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const AUTH = join(appRoot, "app", "(auth)");
const CALLBACK = join(appRoot, "app", "auth", "callback", "route.ts");
const ADMIN = join(appRoot, "lib", "supabase", "admin.ts");
const SOURCE = [".ts", ".tsx"];

function files(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (["node_modules", ".next", ".git"].includes(entry.name)) continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) found.push(...files(path));
    else if (SOURCE.some((suffix) => entry.name.endsWith(suffix))) found.push(path);
  }
  return found;
}

const read = (file: string) => readFileSync(file, "utf8");
const isClient = (file: string) => /^\s*(?:\/\/[^\n]*\n|\/\*[\s\S]*?\*\/\s*)*["']use client["']/.test(read(file).slice(0, 400));

function importsOf(file: string): string[] {
  const source = read(file);
  const found: string[] = [];
  for (const pattern of [
    /(?:^|\n)\s*(?:import|export)[\s\S]*?from\s+["']([^"']+)["']/g,
    /(?:^|\n)\s*import\s+["']([^"']+)["']/g,
    /\bimport\(\s*["']([^"']+)["']\s*\)/g,
  ]) {
    for (const match of source.matchAll(pattern)) found.push(match[1]);
  }
  return found;
}

function resolveImport(from: string, specifier: string): string | null {
  const base = specifier.startsWith("@/")
    ? join(appRoot, specifier.slice(2))
    : specifier.startsWith(".")
      ? resolve(dirname(from), specifier)
      : null;
  if (base === null) return null;
  for (const candidate of [base, ...SOURCE.map((s) => base + s), ...SOURCE.map((s) => join(base, "index" + s))]) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      continue;
    }
  }
  return null;
}

/**
 * The import trail from `entry` to the service-role client, or null. A `"use server"` module is an
 * action boundary: a client component that imports it gets an RPC stub, not the module's body, so
 * the walk stops there — that is exactly how the onboarding retry reaches the grant.
 */
function trailToAdmin(entry: string): string[] | null {
  const seen = new Set<string>();
  const queue = [[entry]];
  while (queue.length > 0) {
    const trail = queue.shift()!;
    const file = trail[trail.length - 1];
    if (file === ADMIN) return trail;
    if (seen.has(file)) continue;
    seen.add(file);
    if (file !== entry && /^\s*["']use server["']/.test(read(file))) continue;
    for (const specifier of importsOf(file)) {
      const next = resolveImport(file, specifier);
      if (next !== null) queue.push([...trail, next]);
    }
  }
  return null;
}

test("A2-BUNDLE-01 no client component reaches the service-role client, however indirectly", () => {
  const all = files(appRoot);
  const clients = all.filter(isClient);
  assert.ok(clients.some((file) => file.startsWith(AUTH)), "the auth pages have client components; a walk over none proves nothing");
  // The walker is not vacuous: the server-side grant port does reach the admin client.
  assert.ok(trailToAdmin(join(AUTH, "grant.ts")), "the grant port must use the service-role client (server side)");
  const offenders = clients
    .map((file) => trailToAdmin(file))
    .filter((trail): trail is string[] => trail !== null)
    .map((trail) => trail.map((step) => relative(appRoot, step)).join(" → "));
  assert.deepEqual(offenders, [], `a browser bundle would carry the service-role client:\n${offenders.join("\n")}`);
});

test("A2-BUNDLE-02 the grant port refuses to run in a browser and the retry is a server action", () => {
  const grant = read(join(AUTH, "grant.ts"));
  assert.match(grant, /typeof window !== "undefined"/);
  assert.ok(!isClient(join(AUTH, "grant.ts")));
  assert.match(read(join(AUTH, "welcome", "actions.ts")), /^\s*["']use server["']/);
  // The action takes no arguments: the user is the session's, never one a caller names.
  assert.match(read(join(AUTH, "welcome", "actions.ts")), /export async function claimOnboarding\(\)/);
});

test("A2-CB-09 the callback route delegates to the tested flow and claims through the grant port", () => {
  const route = read(CALLBACK);
  assert.match(route, /completeCallback\(/, "the route must run the tested callback decision");
  assert.match(route, /claim:\s*claimSignupGrant\b/, "a verified callback must claim the grant (RV-06: the callback never did)");
  assert.ok(!/error_description/.test(route), "the route must not read the provider's free text");
});

test("A2-COPY-01 public signup exists and the login page no longer says accounts are invitation-only", () => {
  const login = read(join(AUTH, "login", "page.tsx"));
  assert.ok(!/invitation/i.test(login), "signup is public now");
  assert.match(login, /href="\/signup"/);
  assert.match(read(join(AUTH, "signup", "page.tsx")), /SignupForm/);
});

test("A2-COPY-02 no auth page makes a retention, ZDR or 120-second claim, or prechecks a data-use permission", () => {
  for (const file of files(AUTH)) {
    const source = read(file);
    const name = relative(appRoot, file);
    assert.ok(!/\b120\s*(?:-|\s)?s(?:ec(?:ond)?s?)?\b/i.test(source), `${name}: 120-second claim`);
    assert.ok(!/\bZDR\b|zero[- ]data[- ]retention/i.test(source), `${name}: ZDR claim`);
    assert.ok(!/never stores?/i.test(source), `${name}: never-stores claim`);
    assert.ok(!/defaultChecked/.test(source), `${name}: a prechecked control`);
    assert.ok(!/error\??\.message/.test(source), `${name}: raw auth error text shown to the user`);
  }
});

test("A2-A11Y-01 every auth input has a label, and every error region is announced", () => {
  for (const file of files(AUTH).filter((f) => f.endsWith(".tsx"))) {
    const source = read(file);
    const name = relative(appRoot, file);
    for (const match of source.matchAll(/<Input\b[\s\S]*?\bid="([^"]+)"/g)) {
      assert.match(source, new RegExp(`htmlFor="${match[1]}"`), `${name}: input #${match[1]} has no label`);
    }
    for (const match of source.matchAll(/className="[^"]*text-destructive[^"]*"/g)) {
      const at = source.indexOf(match[0]);
      const tag = source.slice(source.lastIndexOf("<", at), at + match[0].length + 40);
      assert.match(tag, /role="alert"|aria-live=/, `${name}: an error region is not announced`);
    }
  }
});

// ---------------------------------------------------------- fix round: the wiring layer ---
// The decisions are tested in onboarding-flow.test.ts; these pin that each page actually calls them.
// (Browser tooling is not in package.json; see the evidence's fix-round section.)

/** assert.match without the whole file in the diagnostic (the mutant runner reads 40 lines of it). */
const has = (source: string, pattern: RegExp, message = `missing ${pattern}`) => assert.ok(pattern.test(source), message);

const MIGRATIONS = join(appRoot, "supabase", "migrations");
const migration = (prefix: string) => read(join(MIGRATIONS, readdirSync(MIGRATIONS).find((name) => name.startsWith(prefix))!));

test("A2-GRANT-05 the grant call matches A1's function (0015) and grant.ts sends it and reads its error", () => {
  const signature = new RegExp(`create or replace function public\\.${CLAIM_RPC}\\(([^)]*)\\)`).exec(migration("0015_"));
  assert.ok(signature, `0015 defines no public.${CLAIM_RPC}`);
  const params = signature[1].split(",").map((param) => param.trim());
  const sent = Object.keys(claimArgs("u"));
  for (const key of sent) assert.ok(params.some((param) => param.startsWith(`${key} `)), `0015 has no parameter ${key}`);
  for (const param of params.filter((p) => !/\bdefault\b/.test(p))) {
    assert.ok(sent.includes(param.split(" ")[0]), `required parameter ${param} is not sent`);
  }
  const grant = read(join(AUTH, "grant.ts"));
  has(grant, /createAdminClient\(\)\.rpc\(CLAIM_RPC, claimArgs\(userId\)\)/);
  has(grant, /claimOutcome\(data, error\)/, "a claim error must reach the mapper");
});

test("A2-WIRE-01 sign-in claims the grant through afterSignIn and goes where it says", () => {
  has(read(join(AUTH, "login", "login-form.tsx")), /router\.push\(await afterSignIn\(claimOnboarding, next\)\);/);
});

test("A2-WIRE-02 signup, resend and reset go through the tested requests with the live auth client, and nothing bypasses them", () => {
  has(
    read(join(AUTH, "signup", "signup-form.tsx")),
    /const settled = await requestSignup\(createClient\(\)\.auth, email, String\(form\.get\("password"\)\), window\.location\.origin\);\s*setPending\(false\);\s*if \(settled === "sent"\) setSentTo\(email\);\s*else setError\(FAILURE_COPY\[settled\]\);/,
  );
  has(
    read(join(AUTH, "verify-email", "resend-form.tsx")),
    /const settled = await requestResend\(createClient\(\)\.auth, address, window\.location\.origin\);/,
  );
  has(
    read(join(AUTH, "forgot-password", "page.tsx")),
    /const settled = await requestReset\(createClient\(\)\.auth, email, window\.location\.origin\);\s*setPending\(false\);[\s\S]*?if \(settled === "sent"\) setSent\(true\);\s*else setError\(FAILURE_COPY\[settled\]\);/,
  );
  for (const file of files(AUTH).filter((f) => !f.endsWith("flow.ts"))) {
    assert.ok(!/\.(signUp|resend|resetPasswordForEmail)\(/.test(read(file)), `${relative(appRoot, file)} calls the auth service directly`);
  }
});

test("A2-WIRE-03 resend cools down for 60 s, and an expired reset session offers a new link", () => {
  const resend = read(join(AUTH, "verify-email", "resend-form.tsx"));
  has(resend, /const COOLDOWN_MS = 60_000;/);
  has(resend, /setCoolingDown\(true\);\s*setTimeout\(\(\) => setCoolingDown\(false\), COOLDOWN_MS\);/);
  has(resend, /disabled=\{pending \|\| coolingDown\}/);
  const update = read(join(AUTH, "update-password", "page.tsx"));
  has(update, /setExpired\(failure === "link_expired"\);/);
  has(update, /\{expired \? \(\s*<Link href="\/forgot-password"/);
});

test("A2-WIRE-04 /welcome shows the balance only from welcomeWallet's `available` answer, through displayCredit, never a float", () => {
  has(migration("0008_"), /function public\.console_wallet_summary\(p_user uuid\)/);
  const page = read(join(AUTH, "welcome", "page.tsx"));
  has(page, /const wallet = await welcomeWallet\(\(\) => supabase\.rpc\("console_wallet_summary", \{ p_user: user\.id \}\)\);/);
  assert.equal(page.split("displayCredit(").length - 1, 1, "one place renders the amount");
  has(page, /\{wallet\.kind === "available" \? \(\s*<>\s*<p[^>]*>\s*\{displayCredit\(wallet\.available\)\}/);
  for (const banned of [/\bNumber\(/, /parseFloat\(/, /parseInt\(/, /toFixed\(/, /toLocaleString\(/, /"0(?:\.0+)?"/]) {
    assert.ok(!banned.test(page), `page.tsx: ${banned}`);
  }
});

test("A2-A11Y-02 after signup, focus moves to the 'Check your email' heading", () => {
  const signup = read(join(AUTH, "signup", "signup-form.tsx"));
  has(signup, /useEffect\(\(\) => \{\s*if \(sentTo\) heading\.current\?\.focus\(\);\s*\}, \[sentTo\]\);/);
  has(signup, /<h2 ref=\{heading\} tabIndex=\{-1\}/);
});
