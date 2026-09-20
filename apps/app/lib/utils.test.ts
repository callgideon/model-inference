// node --test lib/utils.test.ts
import assert from "node:assert/strict";
import test from "node:test";
import { safeNext } from "./utils.ts";

test("safeNext keeps same-site paths and rejects everything else", () => {
  assert.equal(safeNext("/usage?range=7d"), "/usage?range=7d");
  assert.equal(safeNext("/update-password"), "/update-password");
  assert.equal(safeNext("https://evil.example"), "/models");
  assert.equal(safeNext("//evil.example"), "/models");
  assert.equal(safeNext(null), "/models");
  assert.equal(safeNext(undefined, "/login"), "/login");
});
