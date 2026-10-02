// node --test "tests/**/*.test.ts"
//
// UX-04 (C-02/C-06, audit UX-A06/A09): the copyable examples carry no credential and no runnable-looking
// fake input. Each reads INFRX_API_KEY from the environment and stops before any request without it;
// the video URL is a visible placeholder until the reader supplies one, and a supplied URL reaches the
// API byte-for-byte whatever shell, Python or JavaScript metacharacters it holds. The async example
// waits the 202's Retry-After before each status read (and a 429/503's own Retry-After), stops at its
// poll bound, and never reads a result after a failed, cancelled or expired job.
//
// Oracles: a snippet with a `{{KEY}}`/prefix fallback, an `example.com` URL, a URL spliced unescaped
// into code, an unbounded or fixed-sleep loop, or a result read after a failure fails here.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import {
  buildExamples,
  VIDEO_URL_PLACEHOLDER,
  videoUrlInput,
  type Example,
  type Language,
} from "../../../app/(console)/docs/examples.ts";
import { parsePublishedModel } from "../../../lib/contracts/v2/published-model.ts";
import { startFakeGateway } from "../../a/fake-gateway.ts";

const run = promisify(execFile);
const PUBLISHED = new URL("../../../../infrx-api/infrx/contracts/v2/published/", import.meta.url);
const MODEL = parsePublishedModel(JSON.parse(readFileSync(new URL("published_marlin_credit.json", PUBLISHED), "utf8")));
const KEY = "sk-infrx-uxfirstcallkey000000000000000000";
const LANGUAGES: Language[] = ["curl", "python", "javascript"];
const RUNNERS: Record<Language, { file: string; cmd: (file: string) => [string, string[]] }> = {
  curl: { file: "example.sh", cmd: (f) => ["bash", ["-euo", "pipefail", f]] },
  python: { file: "example.py", cmd: (f) => ["python3", [f]] },
  javascript: { file: "example.mjs", cmd: (f) => [process.execPath, [f]] },
};
const VIDEO_EXAMPLES = ["video", "stream", "async", "resume"];

const byId = (examples: Example[], id: string) => examples.find((e) => e.id === id)!;

/** Run one snippet with `baseUrl` substituted; `key` null runs it with no INFRX_API_KEY at all. */
async function execute(source: string, lang: Language, baseUrl: string, dir: string, key: string | null) {
  const file = join(dir, RUNNERS[lang].file);
  writeFileSync(file, source.replaceAll("{{BASE_URL}}", baseUrl));
  const [cmd, args] = RUNNERS[lang].cmd(file);
  const env: Record<string, string> = { PATH: process.env.PATH ?? "", HOME: dir };
  if (key !== null) env.INFRX_API_KEY = key;
  try {
    const { stdout, stderr } = await run(cmd, args, { cwd: dir, timeout: 30_000, env: env as unknown as NodeJS.ProcessEnv });
    return { code: 0, stdout, stderr };
  } catch (error) {
    const e = error as { code?: number; stdout?: string; stderr?: string };
    return { code: typeof e.code === "number" ? e.code : -1, stdout: e.stdout ?? "", stderr: e.stderr ?? "" };
  }
}

test("no example carries a credential: each reads INFRX_API_KEY and stops before any request without it", async () => {
  const examples = buildExamples(MODEL, { videoUrl: "https://media.example/clip.mp4" });
  for (const example of examples) {
    for (const lang of LANGUAGES) {
      const source = example.snippets[lang];
      assert.ok(!source.includes("{{KEY}}"), `${example.id} ${lang} has a key placeholder`);
      assert.doesNotMatch(source, /sk-infrx|YOUR_API_KEY|…/, `${example.id} ${lang} carries a key or a prefix`);
      assert.match(source, /INFRX_API_KEY/, `${example.id} ${lang} does not read INFRX_API_KEY`);
    }
  }
  const gateway = await startFakeGateway(MODEL, { keys: [KEY] });
  const dir = mkdtempSync(join(tmpdir(), "ux-first-call-"));
  try {
    for (const lang of LANGUAGES) {
      const out = await execute(byId(examples, "text").snippets[lang], lang, gateway.baseUrl, dir, null);
      assert.notEqual(out.code, 0, `${lang} ran without a key`);
      assert.match(out.stdout + out.stderr, /INFRX_API_KEY/, `${lang} does not say which variable to set`);
    }
    assert.equal(gateway.calls.length, 0, "a request left without a key");
  } finally {
    await gateway.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the connectivity check is a text request, labelled as a connectivity check and not a video test", () => {
  const text = buildExamples(MODEL)[0];
  assert.equal(text.id, "text");
  assert.match(text.title, /connectivity/i);
  assert.match(text.blurb, /not a test of video/i);
  for (const lang of LANGUAGES) assert.doesNotMatch(text.snippets[lang], /video_url|VIDEO_URL/);
});

test("without a supplied URL every video example shows the visible placeholder, and the API refuses it", async () => {
  const examples = buildExamples(MODEL);
  for (const id of VIDEO_EXAMPLES) {
    for (const lang of LANGUAGES) {
      const source = byId(examples, id).snippets[lang];
      assert.ok(source.includes(VIDEO_URL_PLACEHOLDER), `${id} ${lang} has no placeholder`);
      assert.doesNotMatch(source, /example\.com/, `${id} ${lang} carries a runnable-looking demo URL`);
    }
  }
  assert.doesNotMatch(VIDEO_URL_PLACEHOLDER, /^https?:/, "the placeholder must not look like a working URL");
  const gateway = await startFakeGateway(MODEL, { keys: [KEY] });
  const dir = mkdtempSync(join(tmpdir(), "ux-first-call-"));
  try {
    for (const lang of LANGUAGES) {
      const out = await execute(byId(examples, "video").snippets[lang], lang, gateway.baseUrl, dir, KEY);
      assert.notEqual(out.code, 0, `${lang} succeeded with the placeholder`);
    }
    assert.ok(gateway.calls.every((c) => c.status >= 400), "the placeholder was accepted");
  } finally {
    await gateway.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a supplied URL is escaped into each language and reaches the API byte-for-byte", async () => {
  // Each metacharacter where WHATWG URL normalisation keeps it: ' in the path; $( ${ backtick and \ in the
  // query. Unescaped in a shell, the ' closes the quote and both substitutions would create files.
  const raw = "https://media.example/a'b.mp4?q=$(touch${IFS}pwned)&r=`touch${IFS}pwned2`&s=\\x";
  const accepted = videoUrlInput(raw);
  assert.ok(accepted.ok, "a valid https URL was refused");
  const url = accepted.url;
  assert.ok(["'", "$(", "`", "${", "\\"].every((c) => url.includes(c)), "the normalised URL lost the metacharacters under test");
  const examples = buildExamples(MODEL, { videoUrl: url });
  const gateway = await startFakeGateway(MODEL, { keys: [KEY] });
  const dir = mkdtempSync(join(tmpdir(), "ux-first-call-"));
  try {
    for (const lang of LANGUAGES) {
      const before = gateway.calls.length;
      const out = await execute(byId(examples, "video").snippets[lang], lang, gateway.baseUrl, dir, KEY);
      assert.equal(out.code, 0, `${lang} failed: ${out.stderr}`);
      const sent = gateway.calls.slice(before).find((c) => c.route === "/v1/chat/completions")!;
      const parts = (sent.body as { messages: { content: { type: string; video_url?: { url: string } }[] }[] }).messages[0].content;
      assert.equal(parts.find((p) => p.type === "video_url")!.video_url!.url, url, `${lang} changed the URL`);
    }
    assert.ok(!existsSync(join(dir, "pwned")) && !existsSync(join(dir, "pwned2")), "the URL was executed as code");
  } finally {
    await gateway.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the video URL field accepts only an https URL, locally and without fetching it", () => {
  for (const bad of ["", "   ", "http://media.example/clip.mp4", "javascript:alert(1)", "clip.mp4", "file:///etc/passwd", "https://user:pw@media.example/clip.mp4", "data:video/mp4;base64,AAAA"]) {
    assert.equal(videoUrlInput(bad).ok, false, `accepted ${bad.slice(0, 40)}`);
  }
  const ok = videoUrlInput("  https://Media.Example/clip.mp4  ");
  assert.deepEqual(ok, { ok: true, url: "https://media.example/clip.mp4" });
});

/** A loopback job API that plays a script of status replies and records every request with its time. */
async function scriptedJobs(script: { status: number; state?: string; retryAfter?: string }[], acceptRetryAfter: string) {
  const seen: { method: string; path: string; at: number }[] = [];
  let polls = 0;
  const server = createServer((req, res) => {
    seen.push({ method: req.method ?? "", path: req.url ?? "", at: Date.now() });
    req.resume();
    req.on("end", () => {
      const json = (status: number, body: unknown, headers: Record<string, string> = {}) => {
        res.writeHead(status, { "Content-Type": "application/json", ...headers });
        res.end(JSON.stringify(body));
      };
      if (req.method === "POST" && req.url === "/v1/jobs") return json(202, { job_handle: "job_1", idempotency_replayed: false }, { "Retry-After": acceptRetryAfter });
      if (req.method === "GET" && req.url === "/v1/jobs/job_1") {
        const step = script[Math.min(polls++, script.length - 1)];
        if (step.status !== 200) return json(step.status, { error: { code: "dependency_unavailable" } }, step.retryAfter ? { "Retry-After": step.retryAfter } : {});
        return json(200, { job_handle: "job_1", state: step.state, cause: step.state === "failed" ? "engine_error" : null, result_expires_at: null, usage: null });
      }
      if (req.method === "GET" && req.url === "/v1/jobs/job_1/result") return json(200, { response: { choices: [{ message: { content: "RESULT-READ" } }] } });
      json(404, { error: { code: "not_found" } });
    });
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  const { port } = server.address() as AddressInfo;
  return { baseUrl: `http://127.0.0.1:${port}`, seen, close: () => new Promise<void>((done) => server.close(() => done())) };
}

/** The async snippet with its visible poll bound lowered, so a test can watch it give up. */
function asyncSource(lang: Language, maxPolls: number): string {
  const source = byId(buildExamples(MODEL, { videoUrl: "https://media.example/clip.mp4" }), "async").snippets[lang];
  const bound = /^(\s*(?:const )?MAX_POLLS\s*=\s*)\d+/m;
  assert.match(source, bound, `${lang} async example has no visible poll bound`);
  return source.replace(bound, `$1${maxPolls}`);
}

test("async waits the Retry-After hints, never reads a result after a failure, and says why it stopped", async () => {
  for (const lang of LANGUAGES) {
    // The 503's hint (2 s) differs from the 202's (1 s), so honouring the wrong one is visible.
    const jobs = await scriptedJobs([{ status: 503, retryAfter: "2" }, { status: 200, state: "failed" }], "1");
    const dir = mkdtempSync(join(tmpdir(), "ux-first-call-"));
    try {
      const out = await execute(asyncSource(lang, 10), lang, jobs.baseUrl, dir, KEY);
      assert.notEqual(out.code, 0, `${lang} exited 0 after a failed job`);
      assert.match(out.stdout + out.stderr, /failed/, `${lang} did not report the terminal state`);
      assert.ok(!jobs.seen.some((r) => r.path.endsWith("/result")), `${lang} read a result after a failure`);
      const polls = jobs.seen.filter((r) => r.method === "GET");
      assert.equal(polls.length, 2, `${lang} polled ${polls.length} times`);
      const accepted = jobs.seen[0].at;
      assert.ok(polls[0].at - accepted >= 900, `${lang} polled before the 202's Retry-After`);
      assert.ok(polls[1].at - polls[0].at >= 1900, `${lang} retried before the 503's Retry-After`);
    } finally {
      await jobs.close();
      rmSync(dir, { recursive: true, force: true });
    }
  }
});

test("async stops at its poll bound when the job never finishes, without reading a result", async () => {
  for (const lang of LANGUAGES) {
    const jobs = await scriptedJobs([{ status: 200, state: "running" }], "0");
    const dir = mkdtempSync(join(tmpdir(), "ux-first-call-"));
    try {
      const out = await execute(asyncSource(lang, 3), lang, jobs.baseUrl, dir, KEY);
      assert.notEqual(out.code, 0, `${lang} exited 0 while the job was still running`);
      assert.equal(jobs.seen.filter((r) => r.method === "GET" && r.path === "/v1/jobs/job_1").length, 3, `${lang} ignored its bound`);
      assert.ok(!jobs.seen.some((r) => r.path.endsWith("/result")), `${lang} read a result of an unfinished job`);
    } finally {
      await jobs.close();
      rmSync(dir, { recursive: true, force: true });
    }
  }
});

test("async reads the result once the job succeeds", async () => {
  for (const lang of LANGUAGES) {
    const jobs = await scriptedJobs([{ status: 200, state: "running" }, { status: 200, state: "succeeded" }], "0");
    const dir = mkdtempSync(join(tmpdir(), "ux-first-call-"));
    try {
      const out = await execute(asyncSource(lang, 5), lang, jobs.baseUrl, dir, KEY);
      assert.equal(out.code, 0, `${lang} failed: ${out.stderr}`);
      assert.match(out.stdout, /RESULT-READ/, `${lang} did not print the result`);
    } finally {
      await jobs.close();
      rmSync(dir, { recursive: true, force: true });
    }
  }
});
