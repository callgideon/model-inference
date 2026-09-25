// node --test "tests/**/*.test.ts"
//
// U1R: the only seam between a production request and the CREDIT fixture (a plausible 9,9xx-credit
// balance). `previewAllowed` is the gate and `creditSource` is the one place the fixture can be
// chosen; `billing/credit-context.ts` is glue that must route through it. Failure oracle: a gate
// forced open (U1R-M30), a choice that ignores the gate (U1R-M31), or glue that reaches the fixture
// directly would each let production render fake funds, and each fails a case here.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { previewAllowed } from "../../app/(console)/usage/fake-console-context.ts";
import { creditSource, type CreditSource } from "../../app/(console)/billing/credit-fixture.ts";
import type { CreditReads } from "../../app/(console)/billing/credit-reads.ts";

const app = join(dirname(fileURLToPath(import.meta.url)), "../..");
const OPT_IN = { NODE_ENV: "development", INFRX_CONSOLE_PREVIEW: "1" };

function realSource() {
  const source: CreditSource = { reads: {} as CreditReads, preview: false, now: new Date(0) };
  let calls = 0;
  return { source, real: async () => ((calls += 1), source), calls: () => calls };
}

const T = {
  gate: "U1R-G01 the CREDIT fixture gate opens only on an explicit development opt-in",
  source: "U1R-G02 with the gate closed the pages get the real session reads, never the fixture",
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
    const { source, real, calls } = realSource();
    assert.equal(await creditSource(real, env), source, `the fixture was served for ${JSON.stringify(env)}`);
    assert.equal(calls(), 1);
  }
  const { real, calls } = realSource();
  const preview = await creditSource(real, OPT_IN);
  assert.equal(preview.preview, true);
  assert.equal(calls(), 0, "the preview must not open a real session");
  // The page glue has no gate of its own: it can only reach the fixture through creditSource.
  const glue = readFileSync(join(app, "app/(console)/billing/credit-context.ts"), "utf8");
  assert.match(glue, /return creditSource\(async \(\) => \{/);
  assert.doesNotMatch(glue, /fixtureCreditReads|defaultCreditFixture|previewAllowed/);
});

test(T.build, () => {
  const url = (path: string) => JSON.stringify(pathToFileURL(join(app, path)).href);
  const probe =
    `const { previewAllowed } = await import(${url("app/(console)/usage/fake-console-context.ts")});\n` +
    `const { creditSource } = await import(${url("app/(console)/billing/credit-fixture.ts")});\n` +
    `const env = ${JSON.stringify(OPT_IN)};\n` +
    `const got = await creditSource(async () => ({ reads: {}, preview: false, now: new Date(0) }), env);\n` +
    `process.stdout.write(JSON.stringify([previewAllowed(env), previewAllowed(), got.preview]));`;
  const run = spawnSync(process.execPath, ["--input-type=module", "-e", probe], {
    env: { ...process.env, NODE_ENV: "production", INFRX_CONSOLE_PREVIEW: "1" },
    encoding: "utf8",
  });
  assert.equal(run.status, 0, `the probe process failed: ${run.stderr}`);
  assert.equal(run.stdout, "[false,false,false]", "a production build opened the CREDIT fixture");
});
