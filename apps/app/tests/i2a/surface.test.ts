// node --test "tests/**/*.test.ts"
//
// I2A, the static and built half: which responses may be cached, what the browser bundle carries,
// and where the startup check and the release identity are wired. Source checks always run; the
// BUILT-* cases read `.next` and skip visibly until `pnpm build` has run in this tree.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { PRIVATE_NO_STORE, PUBLIC_PAGES, cacheHeaders, isPrivatePath } from "../../lib/deploy/cache.ts";
import { VARIABLES } from "../../lib/deploy/env.ts";
import nextConfig from "../../next.config.ts";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const SOURCE = [".ts", ".tsx"];
const SERVER_ONLY = VARIABLES.filter((v) => v.exposure === "server").map((v) => v.name);
const read = (file: string) => readFileSync(file, "utf8");

function files(directory: string, suffixes = SOURCE): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (["node_modules", ".next", ".git"].includes(entry.name)) continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) found.push(...files(path, suffixes));
    else if (suffixes.some((suffix) => entry.name.endsWith(suffix))) found.push(path);
  }
  return found;
}

const isClient = (file: string) => /^\s*(?:\/\/[^\n]*\n|\/\*[\s\S]*?\*\/\s*)*["']use client["']/.test(read(file).slice(0, 400));

function reachable(entry: string): string[] {
  const seen = new Set<string>();
  const queue = [entry];
  while (queue.length > 0) {
    const file = queue.shift()!;
    if (seen.has(file)) continue;
    seen.add(file);
    // A "use server" module reaches a client only as an action stub, never as its body.
    if (file !== entry && /^\s*["']use server["']/.test(read(file))) continue;
    const source = read(file);
    for (const match of source.matchAll(/(?:from|import)\s*\(?\s*["']([^"']+)["']/g)) {
      const spec = match[1];
      const base = spec.startsWith("@/") ? join(appRoot, spec.slice(2)) : spec.startsWith(".") ? resolve(dirname(file), spec) : null;
      if (base === null) continue;
      const hit = [base, ...SOURCE.map((s) => base + s), ...SOURCE.map((s) => join(base, "index" + s))].find(
        (candidate) => existsSync(candidate) && statSync(candidate).isFile(),
      );
      if (hit) queue.push(hit);
    }
  }
  return [...seen];
}

/** Every page and route handler under app/, as the URL path it serves. */
function appRoutes(): { path: string; file: string }[] {
  return files(join(appRoot, "app"))
    .filter((file) => /(?:^|[/\\])(?:page\.tsx|route\.ts)$/.test(file))
    .map((file) => {
      const segments = relative(join(appRoot, "app"), dirname(file))
        .split(sep)
        .filter((s) => s !== "" && !/^\(.*\)$/.test(s));
      return { path: "/" + segments.join("/"), file };
    });
}

test("I2A-BUNDLE-01 no client module reaches a file that names a server-only variable (source walk)", () => {
  // Catches: a "use client" component importing a module that reads SUPABASE_SERVICE_ROLE_KEY,
  // INFRX_API_BASE_URL or CONSOLE_CURSOR_SECRET (the name, and any logic around it, ships to browsers).
  const clients = [...files(join(appRoot, "app")), ...files(join(appRoot, "components")), ...files(join(appRoot, "lib"))].filter(isClient);
  assert.ok(clients.length > 5, "the walk found no client modules; it would prove nothing");
  assert.deepEqual(SERVER_ONLY.sort(), ["CONSOLE_CURSOR_SECRET", "INFRX_API_BASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]);
  // The walker is not vacuous: the server-only edge does reach the cursor secret's name.
  assert.ok(reachable(join(appRoot, "lib", "services", "server.ts")).some((f) => /CURSOR_SECRET/.test(read(f))));
  const offenders: string[] = [];
  for (const client of clients) {
    for (const file of reachable(client)) {
      const named = SERVER_ONLY.filter((name) => read(file).includes(name));
      if (named.length > 0) offenders.push(`${relative(appRoot, client)} → ${relative(appRoot, file)}: ${named.join(", ")}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test("I2A-ENV-08 every environment variable the App reads is in the matrix or named exempt", () => {
  // Catches: a new server secret wired into a page without appearing in the matrix, the startup
  // check or the runbook.
  const EXEMPT = new Set([
    "NODE_ENV", // Next's own
    "NEXT_RUNTIME", // Next's own
    "INFRX_CONSOLE_PREVIEW", // development-only fixture gate, compiled out of production builds
    "INFRX_API_KEY", // text inside the copyable docs example, not read by the App
    "INFRX_APP_ENVIRONMENT",
    "INFRX_RELEASE_SHA",
    "INFRX_BUILT_AT",
    "VERCEL_ENV",
    "VERCEL_GIT_COMMIT_SHA",
    "VERCEL_DEPLOYMENT_ID",
  ]);
  const known = new Set(VARIABLES.map((v) => v.name));
  const read_: string[] = [];
  const sources = [...files(join(appRoot, "app")), ...files(join(appRoot, "components")), ...files(join(appRoot, "lib"))]
    .concat([join(appRoot, "middleware.ts"), join(appRoot, "instrumentation.ts"), join(appRoot, "next.config.ts")])
    .filter((f) => existsSync(f) && !f.endsWith(".test.ts"));
  for (const file of sources) {
    const source = read(file);
    for (const m of source.matchAll(/process\.env\.([A-Z][A-Z0-9_]+)/g)) read_.push(m[1]);
    for (const m of source.matchAll(/_ENV\s*=\s*"([A-Z][A-Z0-9_]+)"/g)) read_.push(m[1]);
  }
  assert.ok(read_.includes("SUPABASE_SERVICE_ROLE_KEY") && read_.includes("INFRX_API_BASE_URL"), "the scan found nothing");
  assert.deepEqual([...new Set(read_.filter((n) => !known.has(n) && !EXEMPT.has(n)))], []);
});

test("I2A-START-01 the server checks its environment once at startup and fails closed", () => {
  // Catches: the loader existing but never run, so a misconfigured production starts anyway.
  const source = read(join(appRoot, "instrumentation.ts"));
  assert.match(source, /export async function register\(\)/);
  assert.match(source, /assertDeployEnv\(process\.env\)/);
  assert.ok(!/catch/.test(source), "the startup check must not swallow its own refusal");
});

test("I2A-CACHE-01 every response defaults to private, no-store; only static assets are excluded", async () => {
  // Catches: a missing or narrowed header rule, so a route handler or redirect carrying one person's
  // data can be stored by the CDN.
  assert.equal(PRIVATE_NO_STORE, "private, no-store, max-age=0");
  assert.equal(typeof nextConfig.headers, "function", "next.config.ts sets no headers");
  const rules = await nextConfig.headers!();
  assert.deepEqual(rules, cacheHeaders());
  const rule = rules.find((r) => r.headers.some((h) => h.key === "Cache-Control" && h.value === PRIVATE_NO_STORE));
  assert.ok(rule, "no private, no-store rule");
  // Next's source syntax `/:path((?!...).*)` — the capture is a plain regex over the path after "/".
  const inner = /^\/:path\((.*)\)$/.exec(rule.source);
  assert.ok(inner, `unexpected rule source ${rule.source}`);
  const matches = (path: string) => new RegExp(`^${inner[1]}$`).test(path.slice(1));
  for (const { path } of appRoutes()) assert.ok(matches(path), `${path} is outside the private rule`);
  for (const path of ["/usage", "/api/version", "/auth/callback", "/", "/anything/new"]) assert.ok(matches(path), path);
  for (const path of ["/_next/static/chunks/a.js", "/_next/image", "/favicon.ico"]) assert.ok(!matches(path), path);
});

test("I2A-CACHE-02 a page may be cacheable only if it serves no per-person data", () => {
  // Catches: a page listed public (so a static prerender with a shared-cache header is acceptable)
  // that reads the session, cookies or the console services.
  const routes = appRoutes();
  const PERSONAL = /lib[/\\](?:supabase[/\\]server|session|services[/\\]server)\.ts$/;
  for (const path of PUBLIC_PAGES) {
    const route = routes.find((r) => r.path === path);
    assert.ok(route, `${path} is listed public but is not a page`);
    assert.ok(!/\(console\)/.test(route.file), `${path} renders inside the console shell (the balance)`);
    const trail = reachable(route.file).filter((f) => PERSONAL.test(f) || /from\s+["']next\/headers["']/.test(read(f)));
    assert.deepEqual(trail.map((f) => relative(appRoot, f)), [], `${path} reads per-person data`);
  }
  for (const { path } of routes) assert.equal(isPrivatePath(path), !PUBLIC_PAGES.includes(path), path);
  // The console's docs and models pages render the sidebar balance: private, despite public content.
  for (const path of ["/docs", "/models", "/usage", "/billing", "/api-keys", "/traces", "/welcome", "/auth/callback", "/api/version"]) {
    assert.ok(isPrivatePath(path), path);
  }
});

test("I2A-REL-02 the release identity route is private and built from the loader", () => {
  // Catches: a diagnostics route that invents a commit, or that a CDN can cache across releases.
  const route = read(join(appRoot, "app", "api", "version", "route.ts"));
  assert.match(route, /releaseIdentity\(/);
  assert.match(route, /PRIVATE_HEADERS/);
  assert.match(route, /process\.env\.INFRX_RELEASE_SHA/, "the build-time identity must be read textually so Next inlines it");
  assert.match(route, /process\.env\.INFRX_BUILT_AT/);
  const config = read(join(appRoot, "next.config.ts"));
  assert.match(config, /INFRX_RELEASE_SHA:\s*process\.env\.INFRX_RELEASE_SHA \|\| process\.env\.VERCEL_GIT_COMMIT_SHA \|\| ""/);
  assert.match(config, /INFRX_BUILT_AT:/);
});

const NEXT = join(appRoot, ".next");
const built = existsSync(join(NEXT, "BUILD_ID"));

test("I2A-BUILT-01 the built browser bundle names no server-only variable", { skip: built ? false : "no .next build in this tree: run pnpm build first" }, () => {
  // Catches what the source walk cannot see: a server-only name inlined into a client chunk.
  const chunks = files(join(NEXT, "static"), [".js"]);
  assert.ok(chunks.length > 0);
  const offenders = chunks.flatMap((f) => SERVER_ONLY.filter((n) => read(f).includes(n)).map((n) => `${relative(NEXT, f)}: ${n}`));
  assert.deepEqual(offenders, []);
});

test("I2A-BUILT-02 no private page was prerendered as shared static content", { skip: built ? false : "no .next build in this tree: run pnpm build first" }, () => {
  // Catches: a per-person page built static, which Next serves with s-maxage whatever the header rule says.
  const manifest = JSON.parse(read(join(NEXT, "prerender-manifest.json"))) as { routes: Record<string, unknown> };
  const prerendered = Object.keys(manifest.routes).filter((p) => !p.startsWith("/_"));
  assert.ok(prerendered.length > 0, "nothing prerendered; the check would prove nothing");
  assert.deepEqual(prerendered.filter(isPrivatePath), []);
});
