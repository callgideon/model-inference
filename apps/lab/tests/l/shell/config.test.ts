// L1 SPLIT-CONTRACT: the Lab's own origin and session boundary, never widened to reach the App's.
import assert from "node:assert/strict";
import test from "node:test";
import {
  AUTH_COOKIE,
  WORKSPACE_COOKIE,
  authCookieOptions,
  labApiUrl,
  labConfig,
  workspaceCookieOptions,
} from "../../../lib/auth/config.ts";
import nextConfig from "../../../next.config.ts";

const BASE = { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon" };

test("L1-C01 the origin is explicit: development defaults to port 3100, production needs its own https origin", () => {
  assert.equal(labConfig({ ...BASE, NODE_ENV: "development" })?.origin, "http://localhost:3100");
  assert.equal(labConfig({ ...BASE, NODE_ENV: "production" }), null);
  assert.equal(labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "http://lab.example.com" }), null);
  assert.equal(labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "https://lab.example.com/x" }), null);
  assert.equal(labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "https://lab.example.com" })?.origin, "https://lab.example.com");
  assert.equal(labConfig({ NODE_ENV: "development" }), null);
  assert.equal(labConfig({ NODE_ENV: "development", NEXT_PUBLIC_SUPABASE_URL: BASE.NEXT_PUBLIC_SUPABASE_URL }), null);
  assert.equal(labConfig({ NODE_ENV: "development", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon" }), null);
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

test("L1-C03 the Lab session cookie is its own and host-only: never the App's name, never a domain", () => {
  const prod = labConfig({ ...BASE, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: "https://lab.example.com" })!;
  for (const options of [authCookieOptions(prod), workspaceCookieOptions(prod)]) {
    assert.equal("domain" in options, false);
    assert.equal(options.path, "/");
    assert.equal(options.sameSite, "lax");
  }
  assert.equal(authCookieOptions(prod).name, AUTH_COOKIE);
  // The App uses @supabase/ssr's default name, sb-<project ref>-auth-token.
  assert.doesNotMatch(AUTH_COOKIE, /^sb-[a-z0-9]+-auth-token$/);
  assert.notEqual(AUTH_COOKIE, WORKSPACE_COOKIE);
});

test("L1-C04 the workspace preference cookie is http-only and bounded", () => {
  const dev = labConfig({ ...BASE, NODE_ENV: "development" })!;
  assert.equal(workspaceCookieOptions(dev).httpOnly, true);
  assert.equal(workspaceCookieOptions(dev).maxAge, 60 * 60 * 24 * 30);
});

test("L1-C05 every Lab response is private and no-store, and does not advertise the framework", async () => {
  assert.equal(nextConfig.poweredByHeader, false);
  const rules = await nextConfig.headers!();
  assert.deepEqual(rules, [{ source: "/:path*", headers: [{ key: "Cache-Control", value: "private, no-store" }] }]);
});

// LAB-03: one server-only lab-api base URL. Each family's old name is the fallback until the deploy env
// switches, so today's deployment (the six names, one value) keeps working unchanged.
test("L1-C06 every Lab family reads LAB_API_URL first, then only its own old name; an empty value is unset", () => {
  const OLD = {
    control: "LAB_CONTROL_URL", traces: "LAB_TRACES_API_URL", evaluation: "LAB_EVALS_API_URL",
    pipelines: "LAB_PIPELINES_API_URL", releases: "LAB_RELEASES_API_URL", datasets: "LAB_DATASETS_API_URL",
  } as const;
  for (const [family, name] of Object.entries(OLD) as [keyof typeof OLD, string][]) {
    assert.equal(labApiUrl({ LAB_API_URL: "https://one.example" }, family), "https://one.example", family);
    assert.equal(labApiUrl({ [name]: "https://old.example" }, family), "https://old.example", `${family}: ${name} is the fallback`);
    assert.equal(labApiUrl({ LAB_API_URL: "https://one.example", [name]: "https://old.example" }, family), "https://one.example", `${family}: LAB_API_URL wins`);
    assert.equal(labApiUrl({ LAB_API_URL: "", [name]: "https://old.example" }, family), "https://old.example", `${family}: an empty LAB_API_URL is unset`);
    assert.equal(labApiUrl({ [name]: "" }, family), null, `${family}: an empty old name is unset`);
    assert.equal(labApiUrl({}, family), null, `${family}: nothing set`);
    const others = Object.values(OLD).filter((n) => n !== name);
    assert.equal(labApiUrl(Object.fromEntries(others.map((n) => [n, "https://other.example"])), family), null, `${family} never reads another family's name`);
  }
});
