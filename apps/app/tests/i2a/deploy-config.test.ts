// node --test "tests/**/*.test.ts"
//
// I2A: the App's deployment configuration as code — the environment matrix and its fail-closed
// startup check, the auth callback origins per environment, the P-05 settings the code expects,
// and the release identity. Every case names the broken behaviour it catches.
import assert from "node:assert/strict";
import test from "node:test";

import {
  ENVIRONMENTS,
  P05_SETTINGS,
  PREVIEW_SCOPE,
  PRODUCTION_ORIGIN,
  PRODUCTION_SUPABASE_URL,
  REDIRECT_ALLOWLIST,
  VARIABLES,
  assertDeployEnv,
  environmentOf,
  originAllowed,
  releaseIdentity,
} from "../../lib/deploy/env.ts";
import { MIN_PASSWORD_LENGTH, resetRedirect, verifyRedirect } from "../../app/(auth)/flow.ts";

// Placeholder values only; the point of several cases is that none of them is ever echoed.
const SECRET = "placeholder-service-role-0123456789";
const CURSOR = "placeholder-cursor-secret-0123456789";
const STAGING_SUPABASE = "https://stagingref0000000000.supabase.co";
const PREVIEW_ORIGIN = `https://infrx-app-git-feature-x-${PREVIEW_SCOPE}.vercel.app`;

const PRODUCTION = Object.freeze({
  VERCEL_ENV: "production",
  NODE_ENV: "production",
  NEXT_PUBLIC_SUPABASE_URL: PRODUCTION_SUPABASE_URL,
  NEXT_PUBLIC_SUPABASE_ANON_KEY: "placeholder-publishable",
  NEXT_PUBLIC_APP_URL: PRODUCTION_ORIGIN,
  SUPABASE_SERVICE_ROLE_KEY: SECRET,
  INFRX_API_BASE_URL: "https://api.example.test",
  CONSOLE_CURSOR_SECRET: CURSOR,
});

const PREVIEW = Object.freeze({
  VERCEL_ENV: "preview",
  NODE_ENV: "production",
  NEXT_PUBLIC_SUPABASE_URL: STAGING_SUPABASE,
  NEXT_PUBLIC_SUPABASE_ANON_KEY: "placeholder-publishable",
  SUPABASE_SERVICE_ROLE_KEY: SECRET,
});

type Env = Record<string, string | undefined>;

/** The refusal's message, asserting it never carries a value it was given. */
function refusal(env: Env): string {
  let message = "";
  assert.throws(
    () => assertDeployEnv(env),
    (error: Error) => {
      message = error.message;
      return true;
    },
  );
  for (const value of [SECRET, CURSOR]) assert.ok(!message.includes(value), "a refusal echoed a secret value");
  return message;
}

test("I2A-ENV-01 a complete production configuration starts", () => {
  // Catches: a loader that refuses the documented production matrix (the App would never start).
  assert.equal(assertDeployEnv({ ...PRODUCTION }), "production");
  assert.equal(assertDeployEnv({ ...PREVIEW }), "preview");
  assert.equal(assertDeployEnv({ NODE_ENV: "development", NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:54321", NEXT_PUBLIC_SUPABASE_ANON_KEY: "x" }), "development");
});

test("I2A-ENV-02 production missing any required server-only variable fails closed, naming it", () => {
  // Catches: production starting without the service key, API origin or cursor secret and failing
  // later on a user's request (or signing cursors with nothing).
  const required = VARIABLES.filter((v) => v.exposure === "server" && v.required.includes("production"));
  assert.deepEqual(
    required.map((v) => v.name).sort(),
    ["CONSOLE_CURSOR_SECRET", "INFRX_API_BASE_URL", "SUPABASE_SERVICE_ROLE_KEY"],
  );
  for (const { name } of required) {
    for (const missing of [undefined, "", "   "]) {
      assert.match(refusal({ ...PRODUCTION, [name]: missing }), new RegExp(name), `${name}=${JSON.stringify(missing)} was accepted`);
    }
  }
  for (const name of ["NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_ANON_KEY"]) {
    assert.match(refusal({ ...PRODUCTION, [name]: undefined }), new RegExp(name));
  }
  // Fix round (1-I2A-R2): nothing reads NEXT_PUBLIC_APP_URL, so its absence must not take
  // production down (every request 500); a value that is set is still checked (I2A-ENV-04).
  assert.equal(assertDeployEnv({ ...PRODUCTION, NEXT_PUBLIC_APP_URL: undefined }), "production");
});

test("I2A-ENV-03 the production API origin is https with no path, credentials, query or fragment", () => {
  // Catches: docs examples built on a plaintext or pathful origin (`$BASE/v1/...` would double the path).
  for (const bad of [
    "http://api.example.test",
    "http://127.0.0.1:8000", // loopback http is a development convenience only
    "https://api.example.test/v1",
    "https://user:pw@api.example.test",
    "https://api.example.test?x=1",
    "not a url",
  ]) {
    assert.match(refusal({ ...PRODUCTION, INFRX_API_BASE_URL: bad }), /INFRX_API_BASE_URL/, bad);
  }
  assert.equal(assertDeployEnv({ ...PRODUCTION, INFRX_API_BASE_URL: "https://api.example.test/" }), "production");
  // The cursor secret keeps the fail-closed length lib/services/server.ts enforces.
  assert.match(refusal({ ...PRODUCTION, CONSOLE_CURSOR_SECRET: "short" }), /CONSOLE_CURSOR_SECRET/);
});

test("I2A-ENV-04 production is bound to the production project and the production origin", () => {
  // Catches: a production deploy pointed at a staging auth project, or building callbacks for another host.
  assert.match(refusal({ ...PRODUCTION, NEXT_PUBLIC_SUPABASE_URL: STAGING_SUPABASE }), /NEXT_PUBLIC_SUPABASE_URL/);
  assert.match(refusal({ ...PRODUCTION, NEXT_PUBLIC_APP_URL: PREVIEW_ORIGIN }), /NEXT_PUBLIC_APP_URL/);
  assert.match(refusal({ ...PRODUCTION, NEXT_PUBLIC_APP_URL: "http://app.callbill.ai" }), /NEXT_PUBLIC_APP_URL/);
});

test("I2A-ENV-05 a preview carrying production credentials is refused", () => {
  // Catches: a PR preview writing to production (service-role key, signups, grants) — the
  // production project is recognised by the loader's own marker, never by inspecting a key.
  assert.match(refusal({ ...PREVIEW, NEXT_PUBLIC_SUPABASE_URL: PRODUCTION_SUPABASE_URL }), /production/i);
  assert.match(refusal({ ...PREVIEW, NEXT_PUBLIC_SUPABASE_URL: PRODUCTION_SUPABASE_URL + "/" }), /production/i);
  // Coordinator wiring (verification F2 / I2A-R4): a trailing-dot or upper-cased spelling of the production host is the same project.
  assert.match(refusal({ ...PREVIEW, NEXT_PUBLIC_SUPABASE_URL: PRODUCTION_SUPABASE_URL.replace(".supabase.co", ".supabase.co.") }), /production/i);
  assert.match(refusal({ ...PREVIEW, NEXT_PUBLIC_SUPABASE_URL: PRODUCTION_SUPABASE_URL.toUpperCase() }), /production/i);
  assert.match(refusal({ ...PREVIEW, NEXT_PUBLIC_SUPABASE_URL: PRODUCTION_SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY: undefined }), /production/i);
  assert.match(refusal({ ...PREVIEW, NEXT_PUBLIC_APP_URL: PRODUCTION_ORIGIN }), /NEXT_PUBLIC_APP_URL/);
  assert.match(refusal({ ...PREVIEW, NEXT_PUBLIC_APP_URL: "https://infrx-app-x-someoneelse.vercel.app" }), /NEXT_PUBLIC_APP_URL/);
  assert.equal(assertDeployEnv({ ...PREVIEW, NEXT_PUBLIC_APP_URL: PREVIEW_ORIGIN }), "preview");
});

test("I2A-ENV-06 the environment is stated, never guessed", () => {
  // Catches: a production server with no VERCEL_ENV silently validated as development.
  assert.throws(() => environmentOf({ NODE_ENV: "production" }), /VERCEL_ENV|INFRX_APP_ENVIRONMENT/);
  assert.throws(() => environmentOf({ VERCEL_ENV: "staging" }), /VERCEL_ENV/);
  assert.throws(() => environmentOf({ VERCEL_ENV: "production", INFRX_APP_ENVIRONMENT: "preview" }), /disagree/);
  assert.equal(environmentOf({ NODE_ENV: "production", INFRX_APP_ENVIRONMENT: "production" }), "production");
  assert.equal(environmentOf({ NODE_ENV: "development" }), "development");
  assert.equal(environmentOf({}), "development");
});

test("I2A-ENV-07 the matrix: public names are NEXT_PUBLIC_, server-only names are not", () => {
  // Catches: a server-only secret given a NEXT_PUBLIC_ name (Next would inline it into every bundle).
  assert.ok(VARIABLES.length >= 6);
  for (const v of VARIABLES) {
    assert.equal(v.name.startsWith("NEXT_PUBLIC_"), v.exposure === "public", v.name);
    for (const e of v.required) assert.ok(ENVIRONMENTS.includes(e));
  }
});

// Supabase redirect-allowlist globs: `**` any characters, `*` any run without `.` or `/`.
function globMatches(glob: string, url: string): boolean {
  const pattern = glob
    .split("**")
    .map((part) => part.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join("[^./]*"))
    .join(".*");
  return new RegExp(`^${pattern}$`).test(url);
}
const allowlisted = (project: "production" | "staging", url: string) =>
  REDIRECT_ALLOWLIST[project].some((glob) => globMatches(glob, url));

test("I2A-AUTH-01 production allows only the production origin", () => {
  // Catches: production auth links or NEXT_PUBLIC_APP_URL accepted for a preview, http or localhost host.
  assert.ok(originAllowed("production", PRODUCTION_ORIGIN));
  for (const origin of [PREVIEW_ORIGIN, "http://app.callbill.ai", "http://localhost:3000", "https://app.callbill.ai.evil.test", "https://evil.test"]) {
    assert.ok(!originAllowed("production", origin), origin);
  }
});

test("I2A-AUTH-02 previews are this project's Vercel hosts only", () => {
  // Catches: a lookalike or another team's deployment accepted as a preview.
  assert.ok(originAllowed("preview", PREVIEW_ORIGIN));
  assert.ok(originAllowed("preview", `https://infrx-app-a1b2c3d4e-${PREVIEW_SCOPE}.vercel.app`));
  for (const origin of [
    `http://infrx-app-a1b2c3d4e-${PREVIEW_SCOPE}.vercel.app`,
    "https://infrx-app-a1b2c3d4e-someoneelse.vercel.app",
    `https://infrx-app-a1b2c3d4e-${PREVIEW_SCOPE}.vercel.app.evil.test`,
    `https://other-app-a1b2c3d4e-${PREVIEW_SCOPE}.vercel.app`,
    PRODUCTION_ORIGIN,
  ]) {
    assert.ok(!originAllowed("preview", origin), origin);
  }
  assert.ok(originAllowed("development", "http://localhost:3000"));
  assert.ok(!originAllowed("development", "http://evil.test:3000"));
});

test("I2A-AUTH-03 every callback the auth flows build is on its project's allowlist, and only there", () => {
  // Catches: an allowlist entry that does not cover `/auth/callback?next=...` (signup links would be
  // rejected by the auth service), or a production project that accepts preview/localhost callbacks.
  for (const build of [verifyRedirect, resetRedirect]) {
    assert.ok(allowlisted("production", build(PRODUCTION_ORIGIN)), build(PRODUCTION_ORIGIN));
    assert.ok(allowlisted("staging", build(PREVIEW_ORIGIN)), build(PREVIEW_ORIGIN));
    assert.ok(allowlisted("staging", build("http://localhost:3000")));
    assert.ok(!allowlisted("production", build(PREVIEW_ORIGIN)));
    assert.ok(!allowlisted("production", build("http://localhost:3000")));
    assert.ok(!allowlisted("production", build("https://evil.test")));
    assert.ok(!allowlisted("staging", build("https://infrx-app-x-someoneelse.vercel.app")));
  }
});

test("I2A-AUTH-04 the P-05 settings the code expects are listed by name", () => {
  // Catches: the runbook's operator list drifting from what the flows assume.
  const byName = new Map(P05_SETTINGS.map((s) => [s.setting, s.expected]));
  for (const name of [
    "Site URL",
    "Redirect URLs",
    "Confirm email",
    "Allow new users to sign up",
    "Minimum password length",
    "Custom SMTP",
    "Rate limits",
    "Email templates",
    "infrx.feature_flags.signup_grant",
  ]) {
    assert.ok(byName.has(name), `${name} is not listed`);
  }
  assert.match(String(byName.get("Minimum password length")), new RegExp(`\\b${MIN_PASSWORD_LENGTH}\\b`));
  assert.match(String(byName.get("Site URL")), new RegExp(PRODUCTION_ORIGIN.replace(/\./g, "\\.")));
  assert.match(String(byName.get("Confirm email")), /^on\b/i);
});

test("I2A-REL-01 the release identity comes from the build, never invented", () => {
  // Catches: a build that reports a made-up or free-text commit, or a missing one as a real value.
  const sha = "0123456789abcdef0123456789abcdef01234567";
  assert.deepEqual(
    releaseIdentity({
      INFRX_RELEASE_SHA: sha,
      INFRX_BUILT_AT: "2026-09-25T12:00:00.000Z",
      VERCEL_DEPLOYMENT_ID: "dpl_abc123",
      VERCEL_ENV: "production",
      INFRX_API_BASE_URL: "https://api.example.test",
    }),
    { commit: sha, builtAt: "2026-09-25T12:00:00.000Z", deployment: "dpl_abc123", environment: "production", apiOrigin: "https://api.example.test" },
  );
  assert.deepEqual(releaseIdentity({}), {
    commit: "unknown",
    builtAt: "unknown",
    deployment: "unknown",
    environment: "development",
    apiOrigin: "unknown",
  });
  // The Vercel build variable is the fallback; junk is never echoed back.
  assert.equal(releaseIdentity({ VERCEL_GIT_COMMIT_SHA: sha }).commit, sha);
  assert.equal(releaseIdentity({ INFRX_RELEASE_SHA: "main; rm -rf" }).commit, "unknown");
  assert.equal(releaseIdentity({ INFRX_BUILT_AT: "<script>" }).builtAt, "unknown");
  assert.equal(releaseIdentity({ VERCEL_DEPLOYMENT_ID: "dpl_<x>" }).deployment, "unknown");
  assert.equal(releaseIdentity({ INFRX_API_BASE_URL: "http://api.example.test" }).apiOrigin, "unknown");
  assert.equal(releaseIdentity({ VERCEL_ENV: "staging" }).environment, "unknown");
});

test("I2A-REL-03 the host's commit wins; a disagreeing INFRX_RELEASE_SHA makes the commit unknown", () => {
  // Catches (fix round 1-I2A-R3): a stale INFRX_RELEASE_SHA left in the host's environment
  // relabelling every later deploy with the old commit, so smoke S1 passes against the wrong release.
  const host = "b".repeat(40);
  const stale = "a".repeat(40);
  assert.equal(releaseIdentity({ VERCEL_GIT_COMMIT_SHA: host, INFRX_RELEASE_SHA: stale }).commit, "unknown");
  assert.equal(releaseIdentity({ VERCEL_GIT_COMMIT_SHA: host, INFRX_RELEASE_SHA: host }).commit, host);
  // next.config.ts bakes "" for an absent variable: "" is absent, not a disagreement.
  assert.equal(releaseIdentity({ VERCEL_GIT_COMMIT_SHA: host, INFRX_RELEASE_SHA: "" }).commit, host);
  // Off the host, INFRX_RELEASE_SHA is the build input.
  assert.equal(releaseIdentity({ VERCEL_GIT_COMMIT_SHA: "", INFRX_RELEASE_SHA: stale }).commit, stale);
  // A present but malformed host value is never replaced by the operator's.
  assert.equal(releaseIdentity({ VERCEL_GIT_COMMIT_SHA: "main", INFRX_RELEASE_SHA: stale }).commit, "unknown");
});
