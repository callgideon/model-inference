// node --test "tests/**/*.test.ts"
//
// A2, the static half: what ships in which bundle, and what the auth pages are allowed to say.
// These read source, because the properties are about the bundle and the copy, not about a run.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

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
  assert.match(route, /claimSignupGrant/, "a verified callback must claim the grant (RV-06: the callback never did)");
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
    assert.ok(!/error\.message/.test(source), `${name}: raw auth error text shown to the user`);
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
