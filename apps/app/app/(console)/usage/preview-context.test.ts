import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { test } from "node:test";
import { consoleContext } from "./fake-console-context.ts";

const contextModule = pathToFileURL(join(dirname(fileURLToPath(import.meta.url)), "fake-console-context.ts")).href;

/**
 * The module-level `PRODUCTION_BUILD` gate, which only a separate process can exercise: it is
 * evaluated once at import, and in this run `NODE_ENV` is not "production".
 *
 * `next build` inlines the textual `process.env.NODE_ENV`, so in a production bundle this constant is
 * a literal `true` and the fixture path is dropped entirely; the compiled chunk is the real proof and
 * is quoted in the evidence file. What this case proves is the part the bundler cannot: no value
 * handed to `consoleContext` reopens the gate once the build was a production one.
 */
test("a production build serves no fixtures whatever environment it is handed", () => {
  const probe =
    `const { consoleContext } = await import(${JSON.stringify(contextModule)});\n` +
    `process.stdout.write(JSON.stringify([\n` +
    `  consoleContext({ NODE_ENV: "development", INFRX_CONSOLE_PREVIEW: "1" }),\n` +
    `  consoleContext({ NODE_ENV: "test", INFRX_CONSOLE_PREVIEW: "1" }),\n` +
    `]));`;
  const run = spawnSync(process.execPath, ["--input-type=module", "-e", probe], {
    env: { ...process.env, NODE_ENV: "production", INFRX_CONSOLE_PREVIEW: "1" },
    encoding: "utf8",
  });
  assert.equal(run.status, 0, `the probe process failed: ${run.stderr}`);
  assert.equal(
    run.stdout,
    "[null,null]",
    "a production build must refuse fixture accounts even when the environment it reads says development",
  );
});

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
