// L1 SPLIT-CONTRACT: the Lab's own origin and session boundary, never widened to reach the App's.
// AP-09 09c: the one API origin is LAB_API_URL; the Lab holds no Supabase URL or key.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";
import {
  AUTH_COOKIE,
  REFRESH_COOKIE,
  WORKSPACE_COOKIE,
  authCookieOptions,
  labApiUrl,
  labConfig,
  workspaceCookieOptions,
} from "../../../lib/auth/config.ts";
import nextConfig from "../../../next.config.ts";

const BASE = { LAB_API_URL: "https://lab-control.example" };

test("L1-C01 the origin is explicit: development defaults to port 3100, production needs its own https origin", () => {
  assert.equal(labConfig({ ...BASE, NODE_ENV: "development" })?.origin, "http://localhost:3100");
  assert.equal(labConfig({ ...BASE, NODE_ENV: "development" })?.apiUrl, BASE.LAB_API_URL);
  assert.equal(labConfig({ ...BASE, NODE_ENV: "production" }), null);
  assert.equal(labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "http://lab.example.com" }), null);
  assert.equal(labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "https://lab.example.com/x" }), null);
  assert.equal(labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "https://lab.example.com" })?.origin, "https://lab.example.com");
  // Without the API origin nothing works: misconfigured, never a Supabase fallback.
  assert.equal(labConfig({ NODE_ENV: "development" }), null);
  assert.equal(labConfig({ NODE_ENV: "development", LAB_API_URL: "" }), null);
  assert.equal(labConfig({ NODE_ENV: "development", NEXT_PUBLIC_SUPABASE_URL: "https://x.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon" }), null);
  let unparsable: unknown = "threw";
  assert.doesNotThrow(() => (unparsable = labConfig({ ...BASE, NEXT_PUBLIC_LAB_URL: "not a url" })));
  assert.equal(unparsable, null);
});

test("L1-C02 cookies are secure exactly when the origin is https", () => {
  const dev = labConfig({ ...BASE, NODE_ENV: "development" })!;
  const prod = labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "https://lab.example.com" })!;
  assert.equal(authCookieOptions(dev).secure, false);
  assert.equal(authCookieOptions(prod).secure, true);
  assert.equal(workspaceCookieOptions(prod).secure, true);
});

test("L1-C03 the Lab session cookies are its own, http-only and host-only: never the App's or Supabase's name, never a domain", () => {
  const prod = labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "https://lab.example.com" })!;
  for (const options of [authCookieOptions(prod), workspaceCookieOptions(prod)]) {
    assert.equal("domain" in options, false);
    assert.equal(options.path, "/");
    assert.equal(options.sameSite, "lax");
  }
  // The tokens are the server's to forward: page script never reads them.
  assert.equal(authCookieOptions(prod).httpOnly, true);
  for (const name of [AUTH_COOKIE, REFRESH_COOKIE]) assert.doesNotMatch(name, /^sb-/);
  assert.equal(new Set([AUTH_COOKIE, REFRESH_COOKIE, WORKSPACE_COOKIE]).size, 3);
});

test("L1-C04 the workspace preference and session cookies are http-only and bounded", () => {
  const dev = labConfig({ ...BASE, NODE_ENV: "development" })!;
  assert.equal(workspaceCookieOptions(dev).httpOnly, true);
  assert.equal(workspaceCookieOptions(dev).maxAge, 60 * 60 * 24 * 30);
  assert.equal(authCookieOptions(dev).maxAge, 60 * 60 * 24 * 30);
});

test("L1-C05 every Lab response is private and no-store, and does not advertise the framework", async () => {
  assert.equal(nextConfig.poweredByHeader, false);
  const rules = await nextConfig.headers!();
  assert.deepEqual(rules, [{ source: "/:path*", headers: [{ key: "Cache-Control", value: "private, no-store" }] }]);
});

// Register row 76: the six per-family base-URL names are gone; LAB_API_URL is the one origin.
const OLD = ["LAB_CONTROL_URL", "LAB_TRACES_API_URL", "LAB_EVALS_API_URL", "LAB_PIPELINES_API_URL", "LAB_RELEASES_API_URL", "LAB_DATASETS_API_URL"];
const lab = resolve(import.meta.dirname, "../../..");
const walk = (dir: string): string[] => readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? walk(join(dir, n)) : [join(dir, n)]));

test("L1-C06 every Lab family reads LAB_API_URL only; the six old per-family names are read nowhere; an empty value is unset", () => {
  assert.equal(labApiUrl({ LAB_API_URL: "https://one.example" }), "https://one.example");
  assert.equal(labApiUrl({ LAB_API_URL: "" }), null);
  assert.equal(labApiUrl({}), null);
  assert.equal(labApiUrl(Object.fromEntries(OLD.map((n) => [n, "https://old.example"]))), null);
  const readers = ["lib", "app", "components"].flatMap((d) => walk(join(lab, d)))
    .filter((p) => /\.(ts|tsx)$/.test(p) && !p.endsWith(".test.ts") && OLD.some((n) => readFileSync(p, "utf8").includes(n)));
  assert.deepEqual(readers, []);
});
