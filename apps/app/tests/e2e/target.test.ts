// E3A: the browser journey refuses every target but the runner's loopback App (failure oracle:
// a hosted URL, a wrong host or port, or no URL at all would otherwise be driven by the specs).
import assert from "node:assert/strict";
import test from "node:test";
import { loopback } from "./target.ts";

test("the runner's loopback URL inside the e4b block is the only accepted target", () => {
  assert.equal(loopback("E3A_APP_URL", { E3A_APP_URL: "http://127.0.0.1:56870/" }).origin, "http://127.0.0.1:56870");
  assert.equal(loopback("E3A_APP_URL", { E3A_APP_URL: "http://localhost:56870/" }).origin, "http://localhost:56870");
});

test("unset, hosted, non-loopback, outside the block or with a path is refused", () => {
  for (const value of [
    undefined,
    "",
    "https://app.callbill.ai/",
    "http://localhost.example.com:56870/",
    "http://127.0.0.2:56870/",
    "http://0.0.0.0:56870/",
    "https://127.0.0.1:56870/",
    "http://127.0.0.1:3000/",
    "http://127.0.0.1:56932/",
    "http://127.0.0.1:56800/",
    "http://127.0.0.1:56870/v1",
    "http://user:pw@127.0.0.1:56870/",
    "not a url",
  ]) {
    assert.throws(() => loopback("E3A_APP_URL", { E3A_APP_URL: value }), /E3A_APP_URL/, String(value));
  }
});
