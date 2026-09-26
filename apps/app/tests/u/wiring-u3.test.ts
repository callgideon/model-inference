// app-union-2: the coordinator's U3 wiring, checked at the composition roots (WR-U3-2, WR-U3-4).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = (p: string) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");

test("U3-W02 the shared operator action runs through the audited RPC port as the signed-in operator", () => {
  const actions = src("app/actions.ts");
  assert.match(actions, /operator: operatorRpcPort\(async \(\) => \(await createClient\(\)\) as unknown as OperatorRpcClient\),/);
  assert.doesNotMatch(actions, /createAdminClient|SERVICE_ROLE/, "the operator port must use the operator's own session");
});
