// node --test "tests/**/*.test.ts"
//
// A3: the examples test is only as strong as the fake it runs against. These cases prove the fake
// REFUSES what the gateway refuses, so an example that drifted to an unsupported field, a second
// video, a stream on /v1/jobs or a reused key would fail there rather than pass against a yes-man.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parsePublishedModel } from "../../lib/contracts/v2/published-model.ts";
import { startFakeGateway } from "./fake-gateway.ts";

const MODEL = parsePublishedModel(
  JSON.parse(readFileSync(new URL("../../../infrx-api/infrx/contracts/v2/published/published_marlin_credit.json", import.meta.url), "utf8")),
);
const VIDEO = { type: "video_url", video_url: { url: "https://example.com/clip.mp4" } };
const chat = (extra: Record<string, unknown> = {}, content: unknown = [VIDEO]) => ({
  model: MODEL.id,
  max_tokens: 16,
  messages: [{ role: "user", content }],
  ...extra,
});

async function withGateway(body: (post: (path: string, json: unknown, headers?: Record<string, string>, key?: string) => Promise<{ status: number; code?: string; json: Record<string, unknown> }>) => Promise<void>) {
  const gateway = await startFakeGateway(MODEL, { keys: ["alice", "bob", "gone"], revoked: ["gone"] });
  try {
    await body(async (path, json, headers = {}, key = "alice") => {
      const reply = await fetch(gateway.baseUrl + path, {
        method: "POST",
        headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", ...headers },
        body: JSON.stringify(json),
      });
      const text = await reply.text();
      const parsed = text.startsWith("{") ? JSON.parse(text) : {};
      return { status: reply.status, code: parsed.error?.code, json: parsed };
    });
  } finally {
    await gateway.close();
  }
}

test("the fake refuses an unadvertised parameter, a second video and an unknown model", async () => {
  await withGateway(async (post) => {
    assert.equal((await post("/v1/chat/completions", chat())).status, 200);
    for (const name of MODEL.capability.unsupported_parameters) {
      assert.equal((await post("/v1/chat/completions", chat({ [name]: [] }))).code, "unsupported_parameter", name);
    }
    assert.equal((await post("/v1/chat/completions", chat({}, [VIDEO, VIDEO]))).code, "unsupported_media");
    assert.equal((await post("/v1/chat/completions", chat({ model: "someone/else" }))).code, "not_found");
    assert.equal((await post("/v1/chat/completions", chat({ max_tokens: MODEL.capability.max_output_tokens + 1 }))).code, "invalid_request");
    const video = { type: "video_url", video_url: { url: "file:///etc/passwd" } };
    assert.equal((await post("/v1/chat/completions", chat({}, [video]))).code, "unsupported_media");
  });
});

test("the fake refuses a revoked key, a stream on /v1/jobs and a reused key with another body", async () => {
  await withGateway(async (post) => {
    assert.equal((await post("/v1/chat/completions", chat(), {}, "gone")).status, 401);
    assert.equal((await post("/v1/jobs", chat({ stream: true }))).code, "invalid_request");
    const first = await post("/v1/jobs", chat(), { "Idempotency-Key": "k1" });
    const again = await post("/v1/jobs", chat(), { "Idempotency-Key": "k1" });
    assert.equal(first.status, 202);
    assert.equal(again.json.job_handle, first.json.job_handle);
    assert.equal(again.json.idempotency_replayed, true);
    assert.equal((await post("/v1/jobs", chat({ max_tokens: 17 }), { "Idempotency-Key": "k1" })).code, "idempotency_conflict");
  });
});

test("the fake keeps an upload to its owner and its declared constraints", async () => {
  await withGateway(async (post) => {
    const created = await post("/v1/uploads", { max_bytes: 8, accepted_mime: ["video/mp4"] });
    assert.equal(created.status, 201);
    const handle = created.json.upload_handle as string;
    const ref = { type: "video_url", video_url: { url: `infrx-upload:${handle}` } };
    assert.equal((await post("/v1/chat/completions", chat({}, [ref]))).code, "not_found", "not finalized yet");
    assert.equal((await post(`/v1/uploads/${handle}/complete`, {}, {}, "bob")).code, "not_found", "another tenant");
    assert.equal((await post("/v1/uploads", { max_bytes: 8, accepted_mime: ["image/png"] })).code, "invalid_request");
    assert.equal((await post("/v1/uploads", { max_bytes: 8, accepted_mime: ["video/mp4"], purpose: "x" })).code, "invalid_request");
  });
});
