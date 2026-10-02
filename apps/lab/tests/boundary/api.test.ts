// API-BOUNDARY for the Lab (AP-09 09c/09e, R271), read as source: the Lab reaches product data only
// through the API. No production module calls a database RPC or table (`.rpc(`, `.from("...")`), loads
// a Supabase client or names a Supabase setting; every API call it makes is a documented operation of
// the Lab unit's checked export (or, until that export carries them, the Lab shell's routes the gateway
// exports - lib/api/index.ts ShellPaths, pinned here to apps/infrx-api/openapi/consumer.json). Run by
// L1's mutant runner (tests/l/shell/run-mutants.mjs).
import assert from "node:assert/strict";
import { readdirSync, readFileSync, realpathSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../..");
// The checked exports, found through the repo checkout the linked client lives in (a mutant copy links node_modules).
const repo = resolve(realpathSync(join(lab, "node_modules/@infrx/api-client")), "../..");
const openapi = (name: string) => JSON.parse(readFileSync(join(repo, `apps/infrx-api/openapi/${name}.json`), "utf8")) as { paths: Record<string, Record<string, unknown>>; components: { schemas: Record<string, { properties?: object }> } };

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (/^(node_modules|\.next)$/.test(name)) return [];
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}
/** The Lab's production modules: everything Next builds (tests and fakes for tests excluded). */
const sources = () =>
  [...["app", "lib", "components"].flatMap((d) => walk(join(lab, d))), join(lab, "proxy.ts")]
    .filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.ts$/.test(p))
    .map((path) => ({ path: relative(lab, path), source: readFileSync(path, "utf8") }));

/** The direct-database transports R271 removes from the web apps. */
const BYPASS: [string, RegExp][] = [
  ["a database RPC", /\.rpc\(/],
  ["a PostgREST table read", /\.from\(\s*["'`]/],
  ["a Supabase client", /@supabase\/|createServerClient|createBrowserClient/],
  ["a Supabase setting", /SUPABASE_[A-Z_]+|service_role/],
];
export const bypasses = (files: { path: string; source: string }[]) =>
  files.flatMap(({ path, source }) => BYPASS.filter(([, re]) => re.test(source)).map(([what]) => `${path}: ${what}`));

test("L1-T01 no Lab production module reaches the database: no RPC, no table read, no Supabase client or setting", () => {
  assert.deepEqual(bypasses(sources()), []);
  // The check itself catches each transport, and passes plain array code.
  assert.deepEqual(bypasses([
    { path: "a.ts", source: 'await client.rpc("lab_provider_memberships")' },
    { path: "b.ts", source: 'await client.from("api_keys").select("*")' },
    { path: "c.ts", source: 'import { createServerClient } from "@supabase/ssr";' },
    { path: "d.ts", source: "const url = process.env.NEXT_PUBLIC_SUPABASE_URL;" },
    { path: "e.ts", source: "const rows = Array.from(new Set(ids)); const b = Buffer.from(text);" },
  ]), ["a.ts: a database RPC", "b.ts: a PostgREST table read", "c.ts: a Supabase client", "d.ts: a Supabase setting"]);
});

/** Every `api.call(method, path` in the Lab's production modules. */
const calls = () => sources().flatMap(({ path, source }) =>
  [...source.matchAll(/\.call\(\s*"(get|post|put|delete|patch)",\s*"([^"]+)"/g)].map((m) => ({ path, method: m[1], route: m[2] })));
/** The ShellPaths the Lab types by hand until the Lab unit's export carries them. */
const shell = () => {
  const port = readFileSync(join(lab, "lib/api/index.ts"), "utf8");
  const block = port.slice(port.indexOf("export type ShellPaths = {"), port.indexOf("export type LabApi"));
  return [...block.matchAll(/"(\/[^"]+)": \{\s*(get|post): /g)].map((m) => ({ route: m[1], method: m[2] }));
};

test("L1-T02 every API call the Lab makes is a documented operation: the Lab unit's export, or a Lab-shell route the gateway exports", () => {
  const unit = openapi("lab-control").paths;
  const known = new Set(shell().map((s) => `${s.method} ${s.route}`));
  const found = calls();
  assert.ok(found.length >= 10, `the scan sees the Lab's calls (${found.length})`);
  const undocumented = found.filter((c) => !unit[c.route]?.[c.method] && !known.has(`${c.method} ${c.route}`));
  assert.deepEqual(undocumented, []);
});

test("L1-T03 the hand-typed Lab-shell routes are the gateway export's, path, method and record fields", () => {
  const gateway = openapi("consumer");
  const routes = shell();
  assert.deepEqual(routes.map((r) => `${r.method} ${r.route}`).sort(), [
    "get /auth/v1/callback", "get /lab/v1/capabilities", "get /lab/v1/workspaces", "post /auth/v1/refresh", "post /auth/v1/sign-in", "post /auth/v1/sign-out",
  ]);
  for (const r of routes) assert.ok(gateway.paths[r.route]?.[r.method], `${r.method} ${r.route} is in consumer.json`);
  const fields = (name: string) => Object.keys(gateway.components.schemas[name].properties ?? {}).sort();
  assert.deepEqual(fields("Workspace"), ["capabilities", "provider_name", "provider_org_id", "role"]);
  assert.deepEqual(fields("Session"), ["access_token", "expires_at", "expires_in", "refresh_token", "token_type", "user_id"]);
  assert.deepEqual(fields("LabCapabilities"), ["capabilities", "features", "provider_org_id", "role"]);
});
