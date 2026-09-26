// node --test "tests/**/*.test.ts"
//
// I3: browser and server error monitoring. What a report may carry (release, route, digest, a
// redacted message) and never carries (keys, tokens, emails, URLs, stacks, bodies); the route's
// refusals; the safe error pages; and the documented shape. Every case names what it catches.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  MESSAGE_MAX,
  REPORT_MAX_BYTES,
  REPORT_PATH,
  browserReport,
  limiter,
  parseClientReport,
  redact,
  safeDigest,
  safeRoute,
  serverErrorLine,
  shortCommit,
  type ErrorLine,
} from "../../lib/deploy/report.ts";
import { releaseIdentity } from "../../lib/deploy/env.ts";
import { ErrorView } from "../../lib/deploy/error-view.ts";
import * as route from "../../app/api/client-errors/route.ts";
import { loadCatalog } from "../../app/(console)/models/catalog.ts";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const repoRoot = resolve(appRoot, "../..");
const read = (path: string) => readFileSync(path, "utf8");
const RELEASE = { commit: "a".repeat(40), deployment: "dpl_Abc123", environment: "production" };

// Placeholders shaped like the real thing; the point is that none of them is ever logged.
const KEY = "sk-infrx-AbCdEfGh0123456789AbCdEfGh0123456789AbCd";
const JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXBsYWNlaG9sZGVy";
const EMAIL = "someone.person+tag@example.com";
const DSN = "postgresql://postgres:pw-placeholder@db.example.test:5432/postgres";
const UUID = "0a000000-0000-4000-8000-000000000001";
const HEX = "f".repeat(64);
const SECRETS = [KEY, JWT, EMAIL, DSN, UUID, HEX, "pw-placeholder", "someone.person", "example.com"];

const json = (value: unknown) => JSON.stringify(value);
const post = (body: string, headers: Record<string, string> = {}) =>
  new Request("https://app.example.test" + REPORT_PATH, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body,
  });

function assertClean(text: string) {
  for (const secret of SECRETS) assert.ok(!text.includes(secret), `leaked ${secret.slice(0, 12)}… in ${text}`);
}

test("I3-SAN-01 the sanitiser drops keys, tokens, emails, credentialed URLs, ids and key=value secrets", () => {
  // Catches: a browser error message carrying a pasted API key, a session JWT, the user's email or
  // a DSN reaching the function log (fails-before: no sanitiser existed).
  const message = `failed for ${EMAIL} with ${KEY} Bearer ${JWT} at ${DSN} id ${UUID} hash ${HEX} password=hunter2 token: abc`;
  const out = redact(message, 10_000);
  assertClean(out);
  assert.ok(!out.includes("hunter2") && !/token: abc/.test(out), out);
  for (const marker of ["[email]", "[key]", "[token]", "[url]", "[redacted]"]) assert.ok(out.includes(marker), `${marker} missing: ${out}`);
  assert.equal(redact("plain words stay", 100), "plain words stay", "over-redaction of ordinary text");
  assert.equal(redact("line\nbreak\u0000x", 100), "line break x", "a report must stay one log line");
  assert.equal(redact("ab ".repeat(500), MESSAGE_MAX).length, MESSAGE_MAX + 1, "the cap is not applied");
});

test("I3-SAN-02 only Next's digest shape survives, and a route loses its query, fragment and ids", () => {
  // Catches: a digest field used to smuggle a key, and /auth/callback?token_hash=... logged whole.
  assert.equal(safeDigest("1234567890"), "1234567890");
  assert.equal(safeDigest("1234567890@E394"), "1234567890@E394");
  for (const bad of [KEY, EMAIL, "12 34", "", 42, null, "1".repeat(21), "123@E"]) assert.equal(safeDigest(bad), null, String(bad));
  assert.equal(safeRoute(`/auth/callback?token_hash=${HEX}&type=email#x`), "/auth/callback");
  assert.equal(safeRoute(`/usage/${UUID}`), "/usage/[token]");
  assert.equal(safeRoute("/forgot-password/update-password-and-more-words"), "/forgot-password/update-password-and-more-words");
  assert.equal(safeRoute("https://evil.example/x"), "unknown");
  assert.equal(safeRoute(undefined), "unknown");
});

test("I3-SHAPE-01 the logged line is exactly the documented shape, for browser and server", () => {
  // Catches: a field added to the log (a header, the path with its query, the stack) without the
  // runbook saying so, or a documented field silently dropped.
  const ops = read(join(repoRoot, "infra", "app", "operations.md"));
  const block = /```json\n(\{"event":"app_error"[^\n]*\})\n```/.exec(ops);
  assert.ok(block, "infra/app/operations.md documents no app_error line");
  const documented = JSON.parse(block[1]) as ErrorLine;
  const parsed = parseClientReport("application/json", json({ digest: "1", route: "/usage", message: "x" }), RELEASE);
  assert.equal(parsed.status, 204);
  const browser = (parsed as { line: ErrorLine }).line;
  const error = Object.assign(new Error(`boom ${EMAIL}`), { digest: "99", stack: `at secret ${KEY}` });
  const server = serverErrorLine(error, "/usage/[requestId]", RELEASE);
  for (const produced of [browser, server]) assert.deepEqual(Object.keys(produced), Object.keys(documented));
  assert.deepEqual(server, { event: "app_error", source: "server", ...RELEASE, route: "/usage/[requestId]", digest: "99", message: null });
  assertClean(json(server));
  assert.deepEqual(serverErrorLine("thrown string", "/x", RELEASE).digest, null);
});

test("I3-ROUTE-01 the report route refuses non-JSON, oversized, malformed and cross-origin posts without echoing", async () => {
  // Catches: a log-injection or log-flood endpoint — a body echoed back, a 5 MB post read and
  // logged, a form post from another site accepted (fails-before: no route existed).
  assert.deepEqual(Object.keys(route).sort(), ["POST"], "only POST may be exported (GET etc. get Next's 405)");
  const big = json({ message: "x".repeat(REPORT_MAX_BYTES) });
  const cases: [Request, number][] = [
    [post(json({ message: KEY }), { "content-type": "text/plain" }), 415],
    [post("not json"), 400],
    [post(json([1, 2])), 400],
    [post(big), 413],
    [post(json({ message: KEY }), { origin: "https://evil.example" }), 403],
  ];
  const logged: string[] = [];
  const original = console.error;
  console.error = (...args: unknown[]) => void logged.push(args.join(" "));
  try {
    for (const [request, status] of cases) {
      const reply = await route.POST(request);
      assert.equal(reply.status, status, `${status} expected`);
      assert.equal(await reply.text(), "", "a refusal echoed a body");
      assert.match(reply.headers.get("cache-control") ?? "", /private, no-store/);
    }
    assert.equal(logged.length, 0, "a refused report was logged");
    const accepted = await route.POST(post(json({ digest: "7", route: `/usage/${UUID}?k=${KEY}`, message: `${EMAIL} ${KEY}`, extra: DSN })));
    assert.equal(accepted.status, 204);
    assert.equal(await accepted.text(), "");
  } finally {
    console.error = original;
  }
  assert.equal(logged.length, 1, "one accepted report is one line");
  const line = JSON.parse(logged[0]) as ErrorLine;
  assert.equal(line.source, "browser");
  assert.equal(line.route, "/usage/[token]");
  assert.equal(line.digest, "7");
  assertClean(logged[0]);
  assert.ok(!logged[0].includes("extra"), "an unknown field was logged");
});

test("I3-ROUTE-02 the per-instance ceiling admits the limit and refuses the rest of the window", () => {
  // Catches: an unbounded log sink (the limiter off by one or never resetting).
  const admit = limiter(3, 1000);
  assert.deepEqual([0, 1, 2, 3].map((t) => admit(t)), [true, true, true, false]);
  assert.equal(admit(1000), true, "the window never resets");
});

test("I3-PAGE-01 the error page shows the digest and release, never the message or stack, and offers a retry", () => {
  // Catches: an error page that prints error.message / error.stack (a server message can carry
  // data; a client one can carry anything) or loses the reference the operator greps for.
  const error = Object.assign(new Error(`database said ${EMAIL} ${KEY}`), { digest: "4242@E1", stack: `Error\n at /srv/app ${HEX}` });
  const html = renderToStaticMarkup(createElement(ErrorView, { error, retry: () => undefined }));
  assertClean(html);
  assert.ok(!html.includes("database said") && !html.includes("/srv/app"), html);
  assert.match(html, /Reference 4242@E1 · release (?:[0-9a-f]{7}|unknown)/);
  assert.match(html, /<button type="button"[^>]*>Try again<\/button>/);
  const smuggled = renderToStaticMarkup(createElement(ErrorView, { error: Object.assign(new Error("x"), { digest: KEY }), retry: () => undefined }));
  assert.ok(!smuggled.includes(KEY) && smuggled.includes("Reference none"), smuggled);
  // The page files only mount the view; global-error brings its own document.
  for (const file of ["error.tsx", "global-error.tsx"]) {
    const source = read(join(appRoot, "app", file));
    assert.match(source, /^"use client";/);
    assert.match(source, /<ErrorView \{\.\.\.props\} \/>/);
    assert.ok(!/\.(message|stack)\b/.test(source), `${file} reads the error's message or stack`);
  }
  assert.match(read(join(appRoot, "app", "global-error.tsx")), /<html[\s\S]*<body/);
  // What the page sends is bounded before it leaves the browser.
  const sent = browserReport(Object.assign(new Error("m".repeat(5000)), { digest: KEY }), "/p".repeat(500));
  assert.ok(new TextEncoder().encode(json(sent)).length < REPORT_MAX_BYTES, "a page report nears the limit");
  assert.equal(sent.digest, null);
});

test("I3-REL-01 the page's short commit follows releaseIdentity's rule exactly", () => {
  // Catches: the client copy of the commit rule drifting from I2A's (a stale INFRX_RELEASE_SHA
  // relabelling the page while /api/version says unknown).
  const a = "a".repeat(40), b = "b".repeat(40);
  const inputs = [{}, { VERCEL_GIT_COMMIT_SHA: a }, { INFRX_RELEASE_SHA: a }, { VERCEL_GIT_COMMIT_SHA: a, INFRX_RELEASE_SHA: a },
    { VERCEL_GIT_COMMIT_SHA: a, INFRX_RELEASE_SHA: b }, { VERCEL_GIT_COMMIT_SHA: "short" }, { VERCEL_GIT_COMMIT_SHA: "", INFRX_RELEASE_SHA: b },
    { VERCEL_GIT_COMMIT_SHA: "short", INFRX_RELEASE_SHA: a }];
  for (const env of inputs) {
    const full = releaseIdentity(env).commit;
    assert.equal(shortCommit(env), full === "unknown" ? "unknown" : full.slice(0, 7), json(env));
  }
});

test("I3-INST-01 instrumentation keeps the fail-closed startup check and logs server errors through the sanitiser", () => {
  // Catches: onRequestError logging the raw error/request (message, query, cookies), or the I2A
  // startup check lost while extending the file.
  const source = read(join(appRoot, "instrumentation.ts"));
  assert.match(source, /assertDeployEnv\(process\.env\)/);
  assert.match(source, /export const onRequestError: Instrumentation\.onRequestError/);
  assert.match(source, /console\.error\(JSON\.stringify\(serverErrorLine\(error, context\.routePath, runningRelease\(\)\)\)\)/);
  assert.equal((source.match(/console\./g) ?? []).length, 1, "a second log call in instrumentation.ts");
  assert.ok(!/_request\./.test(source), "the request (path with query, headers) must not be read");
});

test("I3-COMPAT-01 a draining or rolled-back gateway's 503 renders the catalog-unavailable state, never a stale list", async () => {
  // Catches: the Models/Docs pages treating the maintenance edge's 503 (or a hung gateway) as a
  // catalog, or caching one across the window (operations.md, Combined checks C5).
  const seen: RequestInit[] = [];
  const maintenance = async (_url: string, init?: RequestInit) => {
    seen.push(init ?? {});
    return new Response(JSON.stringify({ object: "list", data: [] }), { status: 503, headers: { "retry-after": "30" } });
  };
  assert.deepEqual(await loadCatalog("https://api.example.test", maintenance), { status: "unavailable", reason: "unreachable" });
  assert.equal(seen[0].cache, "no-store", "the catalog read may be cached across a maintenance window");
  const hung = async () => {
    throw new DOMException("timed out", "TimeoutError");
  };
  assert.deepEqual(await loadCatalog("https://api.example.test", hung), { status: "unavailable", reason: "unreachable" });
});
