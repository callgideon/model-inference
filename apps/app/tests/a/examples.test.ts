// node --test "tests/**/*.test.ts"
//
// A3 (APP-JOURNEY, SPLIT-CONTRACT): every copyable example on the Docs page RUNS - bash+curl+jq,
// python3 stdlib, node fetch - against a loopback gateway fake that refuses what the published
// record refuses, and the requests they make are held to the gateway's own route table and header
// vocabulary (the generated endpoint document, kept fresh by tests/integration/backend) and to the
// record's parameter set. The three languages must make the SAME requests. The normalized requests
// are committed as `example-calls.json`, which the Python replay against the mounted gateway reads
// (wiring request 1). Regenerate with A3_WRITE_CALLS=1.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { buildExamples, CLIP_FILE, EXAMPLE_MAX_TOKENS, type Example, type Language } from "../../app/(console)/docs/examples.ts";
import { parsePublishedModel } from "../../lib/contracts/v2/published-model.ts";
import { ANSWER_TEXT, startFakeGateway, type Call } from "./fake-gateway.ts";

const run = promisify(execFile);
const PUBLISHED = new URL("../../../infrx-api/infrx/contracts/v2/published/", import.meta.url);
const MODEL = parsePublishedModel(JSON.parse(readFileSync(new URL("published_marlin_credit.json", PUBLISHED), "utf8")));
const ENDPOINT_DOC = readFileSync(new URL("../../../../research/plan/evidence/e/E4B-endpoint.md", import.meta.url), "utf8");
const CALLS_FILE = new URL("./example-calls.json", import.meta.url);

const KEY = "sk-infrx-a3examplekey0000000000000000000000";
const REVOKED = "sk-infrx-a3revokedkey000000000000000000000";
const CLIP = Buffer.concat([Buffer.from("\x00\x00\x00\x18ftypmp42", "latin1"), Buffer.alloc(64)]);
const LANGUAGES: Language[] = ["curl", "python", "javascript"];
const EXAMPLES = buildExamples(MODEL);

/** What each example must print when it worked (the fake answers with the frozen fixtures). */
const EXPECT: Record<string, (out: string) => void> = {
  text: (out) => assert.match(out, new RegExp(ANSWER_TEXT)),
  video: (out) => assert.match(out, new RegExp(ANSWER_TEXT)),
  stream: (out) => assert.ok(out.includes(ANSWER_TEXT) || (out.includes("Two people unload boxes") && out.includes("[DONE]")), out),
  upload: (out) => assert.match(out, new RegExp(ANSWER_TEXT)),
  inline: (out) => assert.match(out, new RegExp(ANSWER_TEXT)),
  async: (out) => {
    assert.match(out, /succeeded/);
    assert.match(out, new RegExp(ANSWER_TEXT));
  },
  resume: (out) => {
    assert.match(out, /true/i, "the same key and body replay the original job");
    assert.match(out, /from a van onto a trolley/, "events replay after the cursor");
    assert.doesNotMatch(out, /id: 1-2\n/, "nothing at or before the cursor is replayed");
  },
  revoke: (out) => assert.match(out, /401/),
};

const RUNNERS: Record<Language, { file: string; cmd: (file: string) => [string, string[]] }> = {
  curl: { file: "example.sh", cmd: (f) => ["bash", ["-euo", "pipefail", f]] },
  python: { file: "example.py", cmd: (f) => ["python3", [f]] },
  javascript: { file: "example.mjs", cmd: (f) => [process.execPath, [f]] },
};

async function execute(example: Example, lang: Language, baseUrl: string, dir: string): Promise<string> {
  const key = example.id === "revoke" ? REVOKED : KEY;
  const source = example.snippets[lang].replaceAll("{{BASE_URL}}", baseUrl).replaceAll("{{KEY}}", key);
  const file = join(dir, RUNNERS[lang].file);
  writeFileSync(file, source);
  const [cmd, args] = RUNNERS[lang].cmd(file);
  const { stdout } = await run(cmd, args, {
    cwd: dir,
    timeout: 30_000,
    env: { PATH: process.env.PATH, HOME: dir, INFRX_API_KEY: key } as unknown as NodeJS.ProcessEnv,
  });
  return stdout;
}

// The route table and header vocabulary of the generated endpoint document.
const ROUTES = new Set(
  [...ENDPOINT_DOC.matchAll(/^\| (GET|POST|PUT|DELETE) \| `([^`]+)` \|/gm)].map((m) => `${m[1]} ${m[2]}`),
);
const HEADERS = new Set(
  (/^## Headers\n\n(.+)$/m.exec(ENDPOINT_DOC)?.[1] ?? "").match(/`[^`]+`/g)?.map((h) => h.slice(1, -1).toLowerCase()),
);
// What any HTTP client adds on its own; everything else a snippet sends must be the API's vocabulary.
const TRANSPORT = new Set(["accept", "accept-encoding", "accept-language", "connection", "content-length", "content-type", "host", "sec-fetch-mode", "transfer-encoding", "user-agent"]);

/** A call as the Python replay needs it: handles and the clip's bytes abstracted. */
function normalize(call: Call) {
  const text = JSON.stringify(call.body)
    ?.replace(/infrx-upload:upl_[A-Za-z0-9_-]{43}/g, "infrx-upload:{upload}")
    .replace(/data:video\/mp4;base64,[A-Za-z0-9+/=]+/g, "data:video/mp4;base64,{clip}");
  return {
    method: call.method,
    route: call.route,
    // The API's own headers with their values (the replay needs Idempotency-Key and Last-Event-ID);
    // the key itself is never recorded.
    headers: Object.fromEntries(
      Object.entries(call.headers)
        .filter(([h]) => !TRANSPORT.has(h) || h === "content-type")
        .map(([h, v]) => [h, h === "authorization" ? "Bearer <key>" : v.split(";")[0].trim()])
        .sort(([a], [b]) => (a < b ? -1 : 1)),
    ),
    body: text === undefined ? null : JSON.parse(text),
    status: call.status,
  };
}

async function runAll() {
  const byLanguage = {} as Record<Language, Record<string, ReturnType<typeof normalize>[]>>;
  for (const lang of LANGUAGES) {
    const gateway = await startFakeGateway(MODEL, { keys: [KEY, REVOKED], revoked: [REVOKED] });
    const dir = mkdtempSync(join(tmpdir(), "a3-examples-"));
    writeFileSync(join(dir, CLIP_FILE), CLIP);
    byLanguage[lang] = {};
    try {
      for (const example of EXAMPLES) {
        const before = gateway.calls.length;
        const out = await execute(example, lang, gateway.baseUrl, dir).catch((error: { stdout?: string; stderr?: string }) => {
          assert.fail(`${lang} ${example.id} failed:\n${error.stderr ?? ""}\n${error.stdout ?? ""}`);
        });
        try {
          EXPECT[example.id](out);
        } catch (error) {
          assert.fail(`${lang} ${example.id} printed the wrong thing:\n${out}\n${(error as Error).message}`);
        }
        byLanguage[lang][example.id] = gateway.calls.slice(before).map(normalize);
      }
    } finally {
      await gateway.close();
      rmSync(dir, { recursive: true, force: true });
    }
  }
  return byLanguage;
}

const results = runAll();
results.catch(() => {}); // each test awaits it and reports the failure

test("every example runs as written in curl, Python and JavaScript and prints its answer", async () => {
  const byLanguage = await results;
  for (const lang of LANGUAGES) assert.deepEqual(Object.keys(byLanguage[lang]), EXAMPLES.map((e) => e.id));
});

test("the three languages make exactly the same requests for each example", async () => {
  const byLanguage = await results;
  for (const example of EXAMPLES) {
    assert.deepEqual(byLanguage.python[example.id], byLanguage.curl[example.id], `${example.id}: python vs curl`);
    assert.deepEqual(byLanguage.javascript[example.id], byLanguage.curl[example.id], `${example.id}: javascript vs curl`);
  }
});

test("every request is a route of the gateway's table with only its header vocabulary", async () => {
  assert.ok(ROUTES.size >= 10 && HEADERS.has("idempotency-key"), "the endpoint document was not parsed");
  const byLanguage = await results;
  for (const lang of LANGUAGES) {
    for (const [id, calls] of Object.entries(byLanguage[lang])) {
      assert.ok(calls.length > 0, `${lang} ${id} made no request`);
      for (const call of calls) {
        assert.ok(ROUTES.has(`${call.method} ${call.route}`), `${lang} ${id}: ${call.method} ${call.route} is not a gateway route`);
        for (const header of Object.keys(call.headers)) {
          assert.ok(header === "content-type" || HEADERS.has(header), `${lang} ${id}: header ${header} is not in the API's vocabulary`);
        }
      }
    }
  }
});

test("every chat body names only advertised parameters, an alias of the model and an in-cap max_tokens", async () => {
  const byLanguage = await results;
  const cap = MODEL.capability;
  assert.ok(EXAMPLE_MAX_TOKENS <= cap.max_output_tokens);
  let bodies = 0;
  for (const calls of Object.values(byLanguage.curl)) {
    for (const call of calls) {
      if (!["/v1/chat/completions", "/v1/jobs"].includes(call.route)) continue;
      const body = call.body as Record<string, unknown>;
      bodies += 1;
      for (const name of Object.keys(body)) {
        assert.ok(cap.parameters.includes(name) && !cap.unsupported_parameters.includes(name), name);
      }
      assert.ok(MODEL.aliases.includes(body.model as string), String(body.model));
      assert.ok((body.max_tokens as number) <= cap.max_output_tokens);
    }
  }
  assert.ok(bodies >= EXAMPLES.length, `only ${bodies} chat bodies`);
});

test("every example but revocation is answered 2xx; revocation is 401 invalid_api_key", async () => {
  const byLanguage = await results;
  for (const lang of LANGUAGES) {
    for (const [id, calls] of Object.entries(byLanguage[lang])) {
      for (const call of calls) {
        if (id === "revoke") assert.equal(call.status, 401);
        else assert.ok(call.status >= 200 && call.status < 300, `${lang} ${id} ${call.route} answered ${call.status}`);
      }
    }
  }
});

test("example-calls.json is the committed record of what the examples send", async () => {
  const byLanguage = await results;
  const generated = `${JSON.stringify({ model: MODEL.id, examples: byLanguage.curl }, null, 2)}\n`;
  if (process.env.A3_WRITE_CALLS === "1") writeFileSync(CALLS_FILE, generated);
  assert.equal(readFileSync(CALLS_FILE, "utf8"), generated, "stale: rerun with A3_WRITE_CALLS=1");
});
