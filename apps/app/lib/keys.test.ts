// node --test lib/keys.test.ts   (Node strips the types; no test framework needed)
import assert from "node:assert/strict";
import test from "node:test";
import { generateKey, hashKey, keyPrefix } from "./keys.ts";

test("keys look like keys and are unique", () => {
  const key = generateKey();
  assert.match(key, /^sk-infrx-[A-Za-z0-9]{40}$/);
  assert.equal(keyPrefix(key), key.slice(0, 17));
  const many = new Set(Array.from({ length: 500 }, generateKey));
  assert.equal(many.size, 500);
});

test("every base62 character can appear (rejection sampling is not truncating the alphabet)", () => {
  const seen = new Set(Array.from({ length: 200 }, generateKey).join("").slice(9));
  assert.ok(seen.size > 55, `only ${seen.size} distinct characters`);
});

test("hash is the sha256 hex the gateway computes", async () => {
  assert.equal(
    await hashKey("abc"),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});
