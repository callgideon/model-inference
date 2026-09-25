// node --test "tests/**/*.test.ts"
//
// The static half of "server only" (C1).
//
// The console's dependency set is frozen, so there is no `server-only` package to import; the modules
// carry a `typeof window` guard, which fails at *run* time. That is one step too late for a bundle: a
// client component that imports `lib/services/query.ts` builds fine and ships every statement — and
// the cursor secret's name — into a browser chunk, where the guard only throws once a user loads the
// page. This walks the import graph instead, so the mistake fails the test run.

import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const SKIP = new Set(["node_modules", ".next", ".git", "out", "build", "coverage", "dist"]);
const SOURCE = [".ts", ".tsx", ".mts"];

function sourceFiles(directory = appRoot): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP.has(entry.name) || entry.name.startsWith(".")) continue;
      found.push(...sourceFiles(join(directory, entry.name)));
    } else if (SOURCE.some((suffix) => entry.name.endsWith(suffix))) {
      found.push(join(directory, entry.name));
    }
  }
  return found;
}

/** Import specifiers, including `export … from` and dynamic `import(...)`. */
function importsOf(file: string): string[] {
  const source = readFileSync(file, "utf8");
  const specifiers: string[] = [];
  for (const pattern of [
    /(?:^|\n)\s*(?:import|export)[\s\S]*?from\s+["']([^"']+)["']/g,
    /(?:^|\n)\s*import\s+["']([^"']+)["']/g,
    /\bimport\(\s*["']([^"']+)["']\s*\)/g,
    /\brequire\(\s*["']([^"']+)["']\s*\)/g,
  ]) {
    for (const match of source.matchAll(pattern)) specifiers.push(match[1]);
  }
  return specifiers;
}

/** Resolve a specifier the way the bundler does: relative paths and the `@/` alias only. */
function resolveImport(from: string, specifier: string): string | null {
  const base = specifier.startsWith("@/")
    ? join(appRoot, specifier.slice(2))
    : specifier.startsWith(".")
      ? resolve(dirname(from), specifier)
      : null;
  if (base === null) return null;
  const candidates = [
    base,
    ...SOURCE.map((suffix) => `${base}${suffix}`),
    ...SOURCE.map((suffix) => join(base, `index${suffix}`)),
    // `allowImportingTsExtensions`: a `./x.ts` specifier is already a file.
    base.replace(/\.ts$/, ".ts"),
  ];
  for (const candidate of candidates) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      continue;
    }
  }
  return null;
}

function isClientModule(file: string): boolean {
  const head = readFileSync(file, "utf8").slice(0, 400);
  return /^\s*(?:\/\/[^\n]*\n|\/\*[\s\S]*?\*\/\s*)*["']use client["']/.test(head);
}

const SERVER_ONLY = join(appRoot, "lib", "services");

/**
 * A `"use server"` module is the one legitimate crossing: the bundler replaces its exports with
 * action references, so its own imports (C3A's `app/actions.ts` -> `lib/services/actions.ts`) never
 * reach a browser chunk. Every other module is followed.
 */
function isServerActionModule(file: string): boolean {
  const head = readFileSync(file, "utf8").slice(0, 400);
  return /^\s*(?:\/\/[^\n]*\n|\/\*[\s\S]*?\*\/\s*)*["']use server["']/.test(head);
}

/** The first path from a client module to a server-only one, or null. */
function pathToServices(entry: string): string[] | null {
  const seen = new Set<string>();
  const queue: { file: string; trail: string[] }[] = [{ file: entry, trail: [entry] }];
  while (queue.length > 0) {
    const { file, trail } = queue.shift()!;
    if (seen.has(file)) continue;
    seen.add(file);
    if (file.startsWith(SERVER_ONLY + "/") || file === SERVER_ONLY) return trail;
    for (const specifier of importsOf(file)) {
      const resolved = resolveImport(file, specifier);
      if (resolved === null) continue;
      if (resolved.startsWith(SERVER_ONLY)) return [...trail, resolved];
      if (isServerActionModule(resolved)) continue;
      queue.push({ file: resolved, trail: [...trail, resolved] });
    }
  }
  return null;
}

test("the walker actually finds client modules and can follow an import", () => {
  const files = sourceFiles();
  assert.ok(files.length > 20, `expected a console tree, found ${files.length} source files`);
  const clients = files.filter(isClientModule);
  assert.ok(clients.length > 0, "the console has client components; a walker that finds none proves nothing");
  // A known edge: the login form is a client component that imports a sibling module.
  const resolved = importsOf(clients[0]).map((specifier) => resolveImport(clients[0], specifier));
  assert.ok(resolved.some((file) => file !== null), "the resolver must resolve at least one local import");
  // And the detector is not simply always-true.
  assert.ok(!isClientModule(join(appRoot, "lib", "services", "query.ts")), "a server module is not a client one");
  // The action boundary is recognised, and only there: a server-only service module is not one.
  assert.ok(isServerActionModule(join(appRoot, "app", "actions.ts")), "app/actions.ts is a server action module");
  assert.ok(!isServerActionModule(join(appRoot, "lib", "services", "actions.ts")), "the adapter is not an action module");
});

test("no client component reaches lib/services, however indirectly", () => {
  const offenders: string[] = [];
  for (const file of sourceFiles().filter(isClientModule)) {
    const trail = pathToServices(file);
    if (trail !== null) offenders.push(trail.map((step) => relative(appRoot, step)).join(" → "));
  }
  assert.deepEqual(
    offenders,
    [],
    `a client bundle would carry the console's SQL, its query plans and the name of the cursor secret:\n${offenders.join("\n")}`,
  );
});

/**
 * The mutation runner decides whether a kill was genuine or merely the boundary guard turning a thrown
 * error into `internal_error`, and it does that from the TAP text of the failing assertion. A case that
 * asserted success as a bare `assert.ok(result.ok)` would print no code at all, and a guard-only failure
 * would be counted as a genuine kill. So every success assertion in this track goes through a helper
 * that names the code, and this is the rule that keeps it that way.
 */
test("every case in this track asserts success through a helper, never as a bare ok check", () => {
  const bare = /assert\.ok\(\s*[^,()]*\.ok\s*\)/;
  const scan = (lines: string[], label: string): string[] => {
    const found: string[] = [];
    lines.forEach((line, index) => {
      const code = line.replace(/\/\/.*$/, "").replace(/^\s*\*.*$/, "");
      // Not the rule's own text: a comment, a doc line or the string literals that demonstrate it.
      if (code.includes("bare.test(") || code.includes("bare =") || code.includes("scan(")) return;
      // `assert.ok(<something>.ok)` with no message: nothing in the output says which code came back.
      if (bare.test(code)) found.push(`${label}:${index + 1}: ${line.trim()}`);
    });
    return found;
  };

  // The scan over a known offender, so switching the scan off is a failure rather than an empty result.
  assert.deepEqual(
    scan(["    assert" + ".ok(result.ok);"], "in-memory"),
    ["in-memory:1: assert" + ".ok(result.ok);"],
    "the scan must actually find the shape it forbids",
  );
  assert.deepEqual(scan(["    assert" + '.ok(result.ok, "named");'], "in-memory"), [], "and allow the named form");

  const offenders: string[] = [];
  for (const file of sourceFiles(join(appRoot, "tests", "c"))) {
    offenders.push(...scan(readFileSync(file, "utf8").split("\n"), relative(appRoot, file)));
  }
  assert.deepEqual(
    offenders,
    [],
    `use expectOk/expectError (or pass a message naming the code) so a guard-only failure cannot be\ncounted as a kill:\n${offenders.join("\n")}`,
  );

});

test("the server-only modules carry their run-time guard as well", () => {
  for (const name of ["query.ts", "console.ts", "cursor.ts", "server.ts", "credits.ts"]) {
    const source = readFileSync(join(SERVER_ONLY, name), "utf8");
    assert.match(
      source,
      /typeof window !== "undefined"/,
      `${name} must refuse to run in a browser as well as being kept out of one`,
    );
  }
});

/**
 * 1-C0-V1. The console layout wraps /admin as well as the consumer pages, so a redirect to onboarding
 * there locks out an operator with no consumer wallet, and a redirect to a route that has not shipped
 * is a 404. `consoleShell` owns both decisions (operator bypass, route gates); the layout may not
 * redirect to either route itself. Holds for today's legacy layout and for WR-1 as revised.
 */
test("the console layout never redirects to onboarding or verification itself; the consumer context goes through consoleShell", () => {
  const layout = readFileSync(join(appRoot, "app", "(console)", "layout.tsx"), "utf8");
  assert.doesNotMatch(layout, /redirect\(\s*["'`]\/(onboarding|verify-email)/, "those redirects are consoleShell's, gated on the route");
  if (/consumerSession\(\)/.test(layout)) {
    assert.match(layout, /consoleShell\(/, "the consumer context reaches the shell only through consoleShell");
    assert.doesNotMatch(layout, /getBalance\(|sidebarBalance\(/, "and the sidebar shows CREDIT, not the legacy USD org summary");
  }
});
