// node --test "tests/**/*.test.ts"
//
// U1R: the only seam between a production request and the client fake (a plausible 9,9xx-credit
// balance). `previewAllowed`/`consoleContext` is the gate and `lib/request-api.ts` `apiSource` the one
// place the fake can be chosen; the page glue must route through it. Failure oracle: a gate forced
// open (U1R-M30), a choice that ignores the gate (U1R-M31), or glue that reaches the fake directly
// would each let production render fake funds, and each fails a case here.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { consoleContext, previewAllowed } from "../../app/(console)/usage/fake-console-context.ts";
import { FAKE_CLOCK } from "../../lib/fake-api.ts";

const app = join(dirname(fileURLToPath(import.meta.url)), "../..");
const OPT_IN = { NODE_ENV: "development", INFRX_CONSOLE_PREVIEW: "1" };

const T = {
  gate: "U1R-G01 the CREDIT fixture gate opens only on an explicit development opt-in",
  source: "U1R-G02 with the gate closed the pages get the real API client, never the client fake",
  build: "U1R-G03 a production build never serves the CREDIT fixture whatever environment it is handed",
};

test(T.gate, () => {
  assert.equal(previewAllowed({ NODE_ENV: "development" }), false, "no opt-in");
  assert.equal(previewAllowed({ NODE_ENV: "development", INFRX_CONSOLE_PREVIEW: "true" }), false, "only '1' opts in");
  assert.equal(previewAllowed({ NODE_ENV: "production", INFRX_CONSOLE_PREVIEW: "1" }), false, "production");
  assert.equal(previewAllowed({ INFRX_CONSOLE_PREVIEW: "1" }), false, "no NODE_ENV is not development");
  assert.equal(previewAllowed(OPT_IN), true);
});

test(T.source, async () => {
  for (const env of [{ NODE_ENV: "development" }, { NODE_ENV: "production", INFRX_CONSOLE_PREVIEW: "1" }, {}]) {
    assert.equal(consoleContext(env), null, `the fake was offered for ${JSON.stringify(env)}`);
  }
  const preview = consoleContext(OPT_IN);
  assert.ok(preview !== null && preview.now.toISOString() === new Date(FAKE_CLOCK).toISOString(), "the preview's clock is the fake's");
  const keys = await preview!.api.call("get", "/console/v1/keys");
  assert.ok(keys.ok && keys.data.data.length === 1, "the preview answers through the generated client");
  // The one place a page's client is chosen: the request's API client unless the gate opens.
  const server = readFileSync(join(app, "lib/request-api.ts"), "utf8");
  assert.match(server, /const preview = consoleContext\(\);\n  if \(preview !== null\) return \{ \.\.\.preview, preview: true \};/);
  // The page glue has no gate of its own: it can only reach the fake through apiSource.
  for (const glue of ["app/(console)/billing/credit-context.ts", "app/(console)/usage/[requestId]/request-context.ts"]) {
    const source = readFileSync(join(app, glue), "utf8");
    assert.match(source, /await apiSource\(\)/, glue);
    assert.doesNotMatch(source, /fakeConsole|defaultWorld|previewAllowed\(|consoleContext\(/, glue);
  }
});

test(T.build, () => {
  const url = (path: string) => JSON.stringify(pathToFileURL(join(app, path)).href);
  const probe =
    `const { previewAllowed, consoleContext } = await import(${url("app/(console)/usage/fake-console-context.ts")});\n` +
    `const env = ${JSON.stringify(OPT_IN)};\n` +
    `process.stdout.write(JSON.stringify([previewAllowed(env), previewAllowed(), consoleContext(env)]));`;
  const run = spawnSync(process.execPath, ["--input-type=module", "-e", probe], {
    env: { ...process.env, NODE_ENV: "production", INFRX_CONSOLE_PREVIEW: "1" },
    encoding: "utf8",
  });
  assert.equal(run.status, 0, `the probe process failed: ${run.stderr}`);
  assert.equal(run.stdout, "[false,false,null]", "a production build opened the CREDIT fixture");
});
