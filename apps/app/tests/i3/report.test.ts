// node --test "tests/**/*.test.ts"
//
// I3: browser and server error monitoring. What a report may carry (release, route with ids
// replaced, digest, the error's class name) and never carries (the message, so no key, token,
// email, URL, stack or body); the route's refusals; the safe error pages; and the documented
// shape. Every case names what it catches.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  REPORT_MAX_BYTES,
  REPORT_PATH,
  browserReport,
  limiter,
  parseClientReport,
  safeDigest,
  safeName,
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
const SECRETS = [KEY, JWT, EMAIL, DSN, UUID, HEX, "pw-placeholder", "someone.person", "example.com", "fffffffff",
  "hunter2", "Some Person", "Some%20Person", "555 0100", "My SSN", "exämple"];
// What the 2026-09-26 review got through the old denylist, word for word (placeholders for the data):
// a percent-encoded email, a JSON-quoted secret, a relative URL with a query, a V8 JSON.parse
// excerpt of a body, a non-ASCII email, and a hex secret cut short by the old client-side cap.
const LEAKY_MESSAGES = [
  "user someone.person%40example.com / Some Person phone +1 415 555 0100",
  '{"password":"hunter2"}',
  "GET /v1/x?email=someone.person%40example.com failed",
  `Unexpected token 'M', "My SSN is "... is not valid JSON`,
  "someone.person@exämple.com failed",
  `x`.repeat(271) + "[token] " + "f".repeat(29),
];

const json = (value: unknown) => JSON.stringify(value);
const post = (body: string | ReadableStream<Uint8Array>, headers: Record<string, string> = {}) =>
  new Request("https://app.example.test" + REPORT_PATH, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body,
    duplex: "half",
  } as RequestInit);

function assertClean(text: string) {
  for (const secret of SECRETS) assert.ok(!text.includes(secret), `leaked ${secret.slice(0, 12)}… in ${text}`);
}

test("I3-SAN-01 a report carries no free text: the message is never sent or logged, only the error's class name", () => {
  // Catches: a browser error message (free text: an email, a pasted key, a JSON.parse excerpt of a
  // body) reaching the function log. Fails-before: the message was sent, redacted by a denylist
  // that each LEAKY_MESSAGES entry got through, and logged.
  for (const message of [...LEAKY_MESSAGES, `failed for ${EMAIL} with ${KEY} Bearer ${JWT} at ${DSN} id ${UUID} hash ${HEX}`]) {
    const error = Object.assign(new TypeError(message), { digest: "5" });
    const sent = json(browserReport(error, "/usage"));
    assertClean(sent);
    assert.deepEqual(JSON.parse(sent), { digest: "5", route: "/usage", name: "TypeError" });
    const parsed = parseClientReport("application/json", json({ digest: "5", route: "/usage", name: "TypeError", message }), RELEASE);
    assert.equal(parsed.status, 204);
    const logged = json((parsed as { line: ErrorLine }).line);
    assertClean(logged);
    assert.ok(!logged.includes('"message"'), `a message field was logged: ${logged}`);
  }
  // Only an identifier-shaped class name survives; a name used to smuggle text is dropped.
  for (const name of ["Error", "TypeError", "ChunkLoadError", "AbortError", "DOMException"]) assert.equal(safeName(name), name);
  for (const bad of [EMAIL, KEY, HEX, "Some Person", "Error: hunter2", "x".repeat(60) + "Error", "Not an error", "", 7, null, undefined])
    assert.equal(safeName(bad), null, String(bad));
});

test("I3-SAN-02 only Next's digest shape survives, and a route keeps only the App's own segments", () => {
  // Catches: a digest field used to smuggle a key; /auth/callback?token_hash=... logged whole; an
  // id, a percent-encoded email or a name in a path segment logged (fails-before: the segment
  // denylist let `someone.person%40example.com` and `Some%20Person` through).
  assert.equal(safeDigest("1234567890"), "1234567890");
  assert.equal(safeDigest("1234567890@E394"), "1234567890@E394");
  for (const bad of [KEY, EMAIL, "12 34", "", 42, null, "1".repeat(21), "123@E"]) assert.equal(safeDigest(bad), null, String(bad));
  assert.equal(safeRoute(`/auth/callback?token_hash=${HEX}&type=email#x`), "/auth/callback");
  assert.equal(safeRoute(`/usage/${UUID}`), "/usage/[id]");
  assert.equal(safeRoute(`/usage/${UUID}/result`), "/usage/[id]/result");
  assert.equal(safeRoute("/a/someone.person%40example.com"), "/a/[id]");
  assert.equal(safeRoute("/a/Some%20Person/x"), "/a/[id]/x");
  assert.equal(safeRoute(`/k/${KEY}`), "/k/[id]");
  assert.equal(safeRoute("/forgot-password/update-password-and-more-words"), "/forgot-password/update-password-and-more-words");
  assert.equal(safeRoute("/"), "/");
  // Next's route patterns (server lines) are code, not data: groups and params stay.
  assert.equal(safeRoute("/(console)/usage/[requestId]/page"), "/(console)/usage/[requestId]/page");
  assert.equal(safeRoute("/_not-found"), "/_not-found");
  assert.equal(safeRoute("https://evil.example/x"), "unknown");
  assert.equal(safeRoute(undefined), "unknown");
  assert.ok(safeRoute("/a".repeat(500)).length <= 201, "the route cap is not applied");
});

test("I3-SHAPE-01 the logged line is exactly the documented shape, for browser and server", () => {
  // Catches: a field added to the log (a header, the path with its query, the stack) without the
  // runbook saying so, or a documented field silently dropped.
  const ops = read(join(repoRoot, "infra", "app", "operations.md"));
  const block = /```json\n(\{"event":"app_error"[^\n]*\})\n```/.exec(ops);
  assert.ok(block, "infra/app/operations.md documents no app_error line");
  const documented = JSON.parse(block[1]) as ErrorLine;
  const parsed = parseClientReport("application/json", json({ digest: "1", route: "/usage", name: "TypeError" }), RELEASE);
  assert.equal(parsed.status, 204);
  const browser = (parsed as { line: ErrorLine }).line;
  const error = Object.assign(new Error(`boom ${EMAIL}`), { digest: "99", stack: `at secret ${KEY}` });
  const server = serverErrorLine(error, "/usage/[requestId]", RELEASE);
  for (const produced of [browser, server]) assert.deepEqual(Object.keys(produced), Object.keys(documented));
  assert.deepEqual(server, { event: "app_error", source: "server", ...RELEASE, route: "/usage/[requestId]", digest: "99", name: "Error" });
  assertClean(json(server));
  assert.deepEqual(serverErrorLine("thrown string", "/x", RELEASE).digest, null);
});

test("I3-ROUTE-01 the report route refuses non-JSON, oversized, malformed and cross-site posts without echoing", async () => {
  // Catches: a log-injection or log-flood endpoint — a body echoed back, a 5 MB post read and
  // logged, a post from another site accepted (fails-before: no route existed).
  assert.deepEqual(Object.keys(route).sort(), ["POST"], "only POST may be exported (GET etc. get Next's 405)");
  const big = json({ name: "x".repeat(REPORT_MAX_BYTES) });
  const cases: [Request, number][] = [
    [post(json({ name: KEY }), { "content-type": "text/plain" }), 415],
    [post("not json"), 400],
    [post(json([1, 2])), 400],
    [post(big), 413],
    [post(json({ name: "TypeError" }), { "content-length": String(REPORT_MAX_BYTES + 1) }), 413],
    [post(json({ name: KEY }), { origin: "https://evil.example", "sec-fetch-site": "cross-site" }), 403],
    [post(json({ name: KEY }), { origin: "https://other.example.test", "sec-fetch-site": "same-site" }), 403],
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
    const accepted = await route.POST(post(json({ digest: "7", route: `/usage/${UUID}?k=${KEY}`, name: "TypeError", message: `${EMAIL} ${KEY}`, extra: DSN })));
    assert.equal(accepted.status, 204);
    assert.equal(await accepted.text(), "");
  } finally {
    console.error = original;
  }
  assert.equal(logged.length, 1, "one accepted report is one line");
  const line = JSON.parse(logged[0]) as ErrorLine;
  assert.equal(line.source, "browser");
  assert.equal(line.route, "/usage/[id]");
  assert.equal(line.digest, "7");
  assert.equal(line.name, "TypeError");
  assertClean(logged[0]);
  assert.ok(!logged[0].includes("extra"), "an unknown field was logged");
});

test("I3-ROUTE-04 a same-origin browser report is accepted whatever host the server sees itself as", async () => {
  // Catches (review 0-I3R-2): comparing Origin with request.url's origin, which is the server's
  // own idea of its host (localhost under `next start`, maybe not the custom domain behind a
  // proxy), so every report from a page browsed at another name was refused 403 and lost.
  // Browsers send Origin on every fetch POST, same-origin included; Sec-Fetch-Site says whether
  // the page and the route share an origin, independent of any proxy.
  const original = console.error;
  console.error = () => undefined;
  try {
    for (const origin of ["http://127.0.0.1:55461", "https://app.callbill.ai", "https://app.example.test"]) {
      const reply = await route.POST(post(json({ name: "TypeError" }), { origin, "sec-fetch-site": "same-origin" }));
      assert.equal(reply.status, 204, origin);
    }
  } finally {
    console.error = original;
  }
});

test("I3-ROUTE-05 a body with no Content-Length is read only up to the limit, then refused", async () => {
  // Catches (review 1-I3R-1): `await request.text()` pulling a whole streamed body into memory
  // before the 2 KiB check (64 MiB pulled before the 413).
  let pulled = 0;
  const chunk = new Uint8Array(1024).fill(0x61);
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      pulled += 1;
      if (pulled > 65_536) controller.close();
      else controller.enqueue(chunk);
    },
  }, { highWaterMark: 0 });
  const request = post(stream);
  assert.equal(request.headers.get("content-length"), null);
  const reply = await route.POST(request);
  assert.equal(reply.status, 413);
  assert.ok(pulled <= 4, `${pulled} KiB pulled before the refusal`);
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
  // What the page sends is bounded and sanitised before it leaves the browser.
  const sent = browserReport(Object.assign(new Error("m".repeat(5000)), { digest: KEY }), "/p".repeat(500));
  assert.ok(new TextEncoder().encode(json(sent)).length < REPORT_MAX_BYTES, "a page report nears the limit");
  assert.equal(sent.digest, null);
  assert.ok(!json(sent).includes("mmm"), "the message left the browser");
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
