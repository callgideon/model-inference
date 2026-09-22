import assert from "node:assert/strict";
import { test } from "node:test";
import { consoleContext } from "./fake-console-context.ts";

test("production cannot serve account fixtures even with the preview flag", () => {
  assert.equal(consoleContext({ NODE_ENV: "production", INFRX_CONSOLE_PREVIEW: "1" }), null);
  assert.equal(consoleContext({ INFRX_CONSOLE_PREVIEW: "1" }), null);
});

test("development previews require explicit opt-in and use a deterministic fixture clock", () => {
  assert.equal(consoleContext({ NODE_ENV: "development" }), null);
  assert.equal(consoleContext({ NODE_ENV: "development", INFRX_CONSOLE_PREVIEW: "true" }), null);
  const first = consoleContext({ NODE_ENV: "development", INFRX_CONSOLE_PREVIEW: "1" });
  const second = consoleContext({ NODE_ENV: "test", INFRX_CONSOLE_PREVIEW: "1" });
  assert.ok(first && second);
  assert.equal(first.now.toISOString(), second.now.toISOString());
  assert.notEqual(first.services, second.services);
});
