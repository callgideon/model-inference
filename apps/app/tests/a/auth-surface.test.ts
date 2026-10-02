// node --test "tests/**/*.test.ts"
//
// A2, the static half: what ships in which bundle, and what the auth pages are allowed to say.
// These read source, because the properties are about the bundle and the copy, not about a run.
// AP-09 09b: the forms reach the auth facade only through server actions; no SDK, no token in a
// browser bundle (the server-side session module is never reachable from a client component).
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const AUTH = join(appRoot, "app", "(auth)");
const CALLBACK = join(appRoot, "app", "auth", "callback", "route.ts");
/** The server-side session: the cookie holding the tokens and the client that forwards them. */
const SERVER_ONLY = new Set([join(appRoot, "app", "(auth)", "session.ts"), join(appRoot, "lib", "request-api.ts")]);
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
 * The import trail from `entry` to a server-only session module, or null. A `"use server"` module
 * is an action boundary: a client component that imports it gets an RPC stub, not the module's
 * body, so the walk stops there — that is exactly how the forms reach the facade.
 */
function trailToServer(entry: string): string[] | null {
  const seen = new Set<string>();
  const queue = [[entry]];
  while (queue.length > 0) {
    const trail = queue.shift()!;
    const file = trail[trail.length - 1];
    if (SERVER_ONLY.has(file)) return trail;
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

test("A2-BUNDLE-01 no client component reaches the server-side session (tokens, cookie), however indirectly", () => {
  const all = files(appRoot);
  const clients = all.filter(isClient);
  assert.ok(clients.some((file) => file.startsWith(AUTH)), "the auth pages have client components; a walk over none proves nothing");
  // The walker is not vacuous: the callback route does reach the session module (server side).
  assert.ok(trailToServer(CALLBACK), "the callback must keep the session through app/(auth)/session.ts");
  const offenders = clients
    .map((file) => trailToServer(file))
    .filter((trail): trail is string[] => trail !== null)
    .map((trail) => trail.map((step) => relative(appRoot, step)).join(" → "));
  assert.deepEqual(offenders, [], `a browser bundle would carry the session module:\n${offenders.join("\n")}`);
});

test("A2-BUNDLE-02 the session module refuses to run in a browser; the forms' entry points are server actions", () => {
  const session = read(join(AUTH, "session.ts"));
  assert.match(session, /typeof window !== "undefined"/);
  assert.ok(!isClient(join(AUTH, "session.ts")));
  for (const actions of [join(AUTH, "auth-actions.ts"), join(AUTH, "welcome", "actions.ts")]) {
    assert.match(read(actions), /^\s*["']use server["']/, relative(appRoot, actions));
  }
  // The retry takes no arguments: the individual is the session's, never one a caller names.
  assert.match(read(join(AUTH, "welcome", "actions.ts")), /export async function claimOnboarding\(\)/);
});

test("A2-CB-09 the callback route delegates to the tested flow, lands through the facade and claims as the new session", () => {
  const route = read(CALLBACK);
  assert.match(route, /completeCallback\(/, "the route must run the tested callback decision");
  has(route, /facadeApi\(null, verifier\)\.call\("get", "\/auth\/v1\/callback"/);
  has(route, /claim: \(session\) => claimGrant\(sessionApi\(session\)\)/, "a verified callback must claim the grant (RV-06)");
  has(route, /response\.cookies\.delete\(VERIFIER_COOKIE\)/, "the PKCE verifier is single-use");
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

test("A2-WIRE-01 sign-in is one server action: facade sign-in, the cookie, then the grant through afterSignIn", () => {
  const actions = read(join(AUTH, "auth-actions.ts"));
  has(actions, /facadeApi\(\)\.call\("post", "\/auth\/v1\/sign-in", \{ body: \{ email, password \} \}\)/);
  has(actions, /await storeSession\(answer\.data\);\s*\/\/[^\n]*\n[^\n]*\n\s*return \{ to: await afterSignIn\(\(\) => claimGrant\(sessionApi\(answer\.data\)\), safeNext\(next\)\) \};/);
  has(read(join(AUTH, "login", "login-form.tsx")), /router\.push\(outcome\.to\);/);
});

test("A2-WIRE-02 signup and reset go through the tested requests with a PKCE challenge, and nothing bypasses the facade", () => {
  const actions = read(join(AUTH, "auth-actions.ts"));
  has(actions, /return requestSignup\(facadeApi\(\), email, String\(form\.get\("password"\) \?\? ""\), await origin\(\), \{\s*codeChallenge: await newChallenge\(\),\s*captchaToken: captchaOf\(form\),/);
  has(actions, /return requestReset\(facadeApi\(\), [^\n]*await origin\(\), \{\s*codeChallenge: await newChallenge\(\),\s*captchaToken: captchaOf\(form\),/);
  has(actions, /return requestResend\(facadeApi\(\), [^\n]*await origin\(\), \{\s*codeChallenge: await newChallenge\(\),\s*captchaToken: captchaOf\(form\),/);
  has(read(join(AUTH, "signup", "signup-form.tsx")), /const settled = await signUp\(form\)/);
  has(read(join(AUTH, "forgot-password", "page.tsx")), /const settled = await recover\(form\)/);
  for (const file of files(AUTH).filter((f) => !f.endsWith("flow.ts"))) {
    assert.ok(!/\.(signUp|resend|resetPasswordForEmail|signInWithPassword|updateUser)\(/.test(read(file)), `${relative(appRoot, file)} calls an auth SDK directly`);
  }
});

test("A2-WIRE-03 an expired reset session offers a new link; resend goes through the tested request and cools down for 60 s", () => {
  const update = read(join(AUTH, "update-password", "page.tsx"));
  has(update, /setExpired\(outcome === "link_expired"\);/);
  has(update, /\{expired \? \(\s*<Link href="\/forgot-password"/);
  has(read(join(AUTH, "auth-actions.ts")), /if \(token === null\) return "link_expired";/, "no session is the expired-link state");
  const resend = read(join(AUTH, "verify-email", "resend-form.tsx"));
  has(resend, /const settled = await resendVerification\(form\)/);
  has(resend, /const COOLDOWN_MS = 60_000;/);
  has(resend, /setCoolingDown\(true\);\s*setTimeout\(\(\) => setCoolingDown\(false\), COOLDOWN_MS\);/);
  has(resend, /disabled=\{pending \|\| coolingDown\}/);
});

test("A2-WIRE-04 /welcome shows the balance only from welcomeWallet's `available` answer, through displayCredit, never a float", () => {
  const page = read(join(AUTH, "welcome", "page.tsx"));
  has(page, /const wallet = await welcomeWallet\(api\);/);
  assert.equal(page.split("displayCredit(").length - 1, 1, "one place renders the amount");
  has(page, /\{wallet\.kind === "available" \? \(\s*<>\s*<p[^>]*>\s*\{displayCredit\(wallet\.available\)\}/);
  for (const banned of [/\bNumber\(/, /parseFloat\(/, /parseInt\(/, /toFixed\(/, /toLocaleString\(/, /"0(?:\.0+)?"/]) {
    assert.ok(!banned.test(page), `page.tsx: ${banned}`);
  }
});

test("LR02-FORM-02 the signup page asks the facade's availability and closes the form honestly when a challenge cannot be shown", () => {
  has(read(join(AUTH, "signup", "page.tsx")), /const gate = captchaGate\(await facade\.call\("get", "\/auth\/v1\/availability"\)/);
  has(read(join(AUTH, "signup", "signup-form.tsx")), /if \(gate === "unconfigured"\) \{\s*return \(\s*<p role="status"[^>]*>\s*\{CAPTCHA_UNAVAILABLE\}/);
  const actions = read(join(AUTH, "auth-actions.ts"));
  assert.equal(actions.split('if (captchaOf(form) === null && (await emailFormGate()) === "unconfigured") return "captcha_unconfigured";').length - 1, 3, "all three email forms (sign-up, recovery, resend) re-check on the server");
});

test("A2-A11Y-02 after signup, focus moves to the 'Check your email' heading", () => {
  const signup = read(join(AUTH, "signup", "signup-form.tsx"));
  has(signup, /useEffect\(\(\) => \{\s*if \(sentTo\) heading\.current\?\.focus\(\);\s*\}, \[sentTo\]\);/);
  has(signup, /<h2 ref=\{heading\} tabIndex=\{-1\}/);
});
