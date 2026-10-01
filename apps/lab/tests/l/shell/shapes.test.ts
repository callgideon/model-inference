// LAB-10: the Lab's input shapes, defined once (lib/services/shapes.ts). Two UUID strictnesses on purpose:
// record ids the Lab mints or forwards are lowercase v4; ids it only reads back accept any version and case.
import assert from "node:assert/strict";
import test from "node:test";
import { USD_RE, UUID, UUID_ANY_RE, UUID_RE } from "../../../lib/services/shapes.ts";

const V4 = "a0000001-0000-4000-8000-000000000001";
const V1 = "a0000001-0000-1000-8000-000000000001";

test("L1-H01 a record id is a lowercase v4 UUID, whole; a read-back id is any UUID in either case, whole", () => {
  assert.equal(UUID_RE.test(V4), true);
  for (const bad of [V1, V4.toUpperCase(), `${V4}x`, `x${V4}`, "a0000001-0000-4000-c000-000000000001", ""]) assert.equal(UUID_RE.test(bad), false, bad);
  assert.equal(new RegExp(`^lab:payer:(${UUID})$`).exec(`lab:payer:${V4}`)?.[1], V4, "UUID composes into refs");
  for (const good of [V4, V1, V4.toUpperCase()]) assert.equal(UUID_ANY_RE.test(good), true, good);
  for (const bad of [`${V4}x`, `x${V4}`, V4.slice(1), "not-a-uuid"]) assert.equal(UUID_ANY_RE.test(bad), false, bad);
});

test("L1-H02 a USD amount is exact to 1e-8: eight decimals, no leading zero, at most twelve integer digits", () => {
  for (const good of ["0.00000000", "10.00000000", "999999999999.12345678"]) assert.equal(USD_RE.test(good), true, good);
  for (const bad of ["10", "10.0", "10.000000001", "01.00000000", "1000000000000.00000000", "-1.00000000", "1e2.00000000", " 1.00000000"])
    assert.equal(USD_RE.test(bad), false, bad);
});
