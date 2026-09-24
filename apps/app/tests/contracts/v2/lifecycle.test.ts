// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// F2C.a, the console half: the Python fixtures decode exactly, a missing or unexpected
// field throws, the empty manifest is never the missing one, and the refusal map is the
// committed table. Fixtures are read from the Python package directory (no copy).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  LIFECYCLE_REFUSALS,
  LIFECYCLE_REFUSAL_CODES,
  READ_OUTCOMES,
  decodeReadinessView,
  decodeTerminalOutcome,
  decodeUploadTicket,
  readOutcome,
  uploadUsable,
} from "../../../lib/contracts/v2/lifecycle.ts";
import * as viaTypes from "../../../lib/contracts/v2/types.ts";

const FIXTURES = new URL("../../../../infrx-api/infrx/contracts/fixtures/v2/", import.meta.url);

function fixture(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(new URL(name, FIXTURES), "utf8"));
}

const without = (obj: Record<string, unknown>, key: string) =>
  Object.fromEntries(Object.entries(obj).filter(([k]) => k !== key));

test("the Python upload tickets decode as the console's DTO", () => {
  const created = decodeUploadTicket(fixture("lifecycle_upload_created.json"));
  const done = decodeUploadTicket(fixture("lifecycle_upload_finalized.json"));
  assert.equal(created.state, "created");
  assert.equal(created.destination_ref, `infrx-upload:${created.upload_handle}`);
  assert.equal(done.finalized?.digest, done.received?.digest);
  assert.equal(done.finalized?.generation, 1);
  assert.equal(viaTypes.decodeUploadTicket, decodeUploadTicket, "reachable through v2/types.ts");
});

test("a missing required or an unexpected field throws, deterministically", () => {
  for (const name of ["lifecycle_upload_created.json", "lifecycle_upload_finalized.json"]) {
    const body = fixture(name);
    for (const key of ["upload_handle", "org_id", "destination_ref", "constraints", "state",
                       "created_at", "expires_at", "schema_version"]) {
      assert.throws(() => decodeUploadTicket(without(body, key)), TypeError, `${name} without ${key}`);
    }
    assert.throws(() => decodeUploadTicket({ ...body, org: "smuggled" }), TypeError);
    assert.throws(() => decodeUploadTicket({ ...body, storage_ref: "media/x" }), TypeError);
    assert.throws(() => decodeUploadTicket({ ...body, schema_version: 1 }), TypeError);
    assert.throws(() => decodeUploadTicket({ ...body, expires_at: null }), TypeError);
  }
  const done = fixture("lifecycle_upload_finalized.json");
  assert.throws(() => decodeUploadTicket(without(done, "finalized")), TypeError, "finalized with no source");
  assert.throws(() => decodeUploadTicket(without(done, "received")), TypeError, "source with no receipt");
  const finalized = done.finalized as Record<string, unknown>;
  assert.throws(() => decodeUploadTicket({ ...done, finalized: without(finalized, "duration_s") }), TypeError);
  assert.throws(() => decodeUploadTicket({ ...done, finalized: { ...finalized, digest: `sha256:${"b2".repeat(32)}` } }), TypeError);
  assert.throws(() => decodeUploadTicket({ ...done, finalized: { ...finalized, mime: "video/x-flv" } }), TypeError);
  // With no declared digest or size, only the receipt says which bytes the handle names.
  const constraints = done.constraints as Record<string, unknown>;
  const loose = { ...done, constraints: without(without(constraints, "digest"), "bytes") };
  assert.equal(decodeUploadTicket(loose).state, "finalized");
  assert.throws(() => decodeUploadTicket({ ...loose, finalized: { ...finalized, digest: `sha256:${"b2".repeat(32)}` } }), TypeError, "forged digest");
  assert.throws(() => decodeUploadTicket({ ...loose, finalized: { ...finalized, bytes: 1 } }), TypeError, "forged size");
  assert.throws(() => decodeUploadTicket({ ...done, destination_ref: "s3://bucket/key" }), TypeError);
  assert.throws(() => decodeUploadTicket({ ...done, created_at: "2026-09-22T12:00:00+00:00" }), TypeError);
  // An aborted ticket carries a public upload reason only - never an internal refusal.
  const created = fixture("lifecycle_upload_created.json");
  assert.equal(decodeUploadTicket({ ...created, state: "aborted", refusal: "media_refused" }).refusal, "media_refused");
  for (const internal of ["claim_lost", "not_ready", "reference_live"]) {
    assert.throws(() => decodeUploadTicket({ ...created, state: "aborted", refusal: internal }), TypeError, internal);
  }
});

test("an empty manifest is ready-with-zero; a missing marker is not_ready", () => {
  const ready = decodeReadinessView(fixture("lifecycle_readiness_view_ready_empty.json"));
  const missing = decodeReadinessView(fixture("lifecycle_readiness_view_not_ready.json"));
  assert.equal(ready.state, "ready");
  assert.equal(ready.source_count, 0);
  assert.equal(missing.state, "not_ready");
  assert.equal("source_count" in missing, false);
  // the confusions the oracle names: each one must throw, never decode
  assert.throws(() => decodeReadinessView(without({ ...ready }, "source_count")), TypeError);
  assert.throws(() => decodeReadinessView({ ...missing, source_count: 0 }), TypeError);
  assert.throws(() => decodeReadinessView({ ...ready, state: "not_ready" }), TypeError);
  assert.throws(() => decodeReadinessView({ ...missing, state: "ready" }), TypeError);
  assert.throws(() => decodeReadinessView({ ...ready, source_count: -1 }), TypeError);
  assert.throws(() => decodeReadinessView({ ...ready, sources: [] }), TypeError);
});

test("a finalized upload is usable strictly before its expiry, never at it", () => {
  const done = decodeUploadTicket(fixture("lifecycle_upload_finalized.json"));
  assert.equal(uploadUsable(done, "2026-09-29T11:59:59.999999Z"), true);
  assert.equal(uploadUsable(done, done.expires_at), false, "equality is expired");
  assert.equal(uploadUsable(decodeUploadTicket(fixture("lifecycle_upload_created.json")),
    "2026-09-22T12:00:01Z"), false, "an unfinalized upload is never usable");
});

test("the refusal map is the committed table, reason for reason", () => {
  const table = fixture("lifecycle_refusals.json") as unknown as { reason: string; code: string }[];
  assert.deepEqual(table.map((row) => row.reason), [...LIFECYCLE_REFUSALS]);
  for (const row of table) {
    assert.equal(LIFECYCLE_REFUSAL_CODES[row.reason as keyof typeof LIFECYCLE_REFUSAL_CODES], row.code, row.reason);
  }
});

// --- F2C.b ---------------------------------------------------------------------------------
// The outcomes come from the v2 table itself (it embeds the v1 terminal fixtures), so the
// suite reads one fixture directory - the one the mutation runner carries beside the app.
type Row = { name: string; now: string; expected: string; outcome?: Record<string, unknown> };
const TABLE = () => fixture("result_read_cases.json") as unknown as Row[];
const v1 = (name: string): Record<string, unknown> => {
  const row = TABLE().find((r) => r.name === name);
  if (!row?.outcome) throw new Error(`no table row ${name}`);
  return row.outcome;
};
const OLD = "pre-F2C.b success at settlement: no expiry is invented";
const NEW = "success at settlement";

test("every read outcome is the committed cross-language table", () => {
  const table = TABLE();
  for (const row of table) {
    const outcome = row.outcome === undefined ? null : decodeTerminalOutcome(row.outcome);
    assert.equal(readOutcome(outcome, row.now), row.expected, row.name);
  }
  assert.deepEqual(new Set(table.map((row) => row.expected)), new Set(READ_OUTCOMES));
});

test("an old terminal record decodes with NO expiry and is never available", () => {
  const old = decodeTerminalOutcome(v1(OLD));
  assert.equal("result_expires_at" in old, false, "no expiry is invented while decoding");
  for (const now of [old.settled_at, "2026-09-20T12:00:10Z", "2026-09-21T12:00:08Z"]) {
    assert.equal(readOutcome(old, now), "unavailable", now);
  }
  const persisted = decodeTerminalOutcome(v1(NEW));
  assert.equal(readOutcome(persisted, persisted.result_expires_at as string), "expired",
    "an expired result is never rendered available at its instant");
});

test("a terminal outcome decodes exactly, and the expiry only where it belongs", () => {
  const body = v1(NEW);
  for (const key of ["job_id", "state", "cause", "settlement_state", "debit", "settled_at", "schema_version"]) {
    assert.throws(() => decodeTerminalOutcome(without(body, key)), TypeError, `without ${key}`);
  }
  assert.throws(() => decodeTerminalOutcome({ ...body, retention: "1d" }), TypeError);
  assert.throws(() => decodeTerminalOutcome({ ...body, schema_version: 2 }), TypeError);
  assert.throws(() => decodeTerminalOutcome({ ...body, result_expires_at: body.settled_at }), TypeError);
  assert.throws(() => decodeTerminalOutcome({ ...v1("terminal_cancelled"),
    result_expires_at: body.result_expires_at }), TypeError);
  assert.throws(() => decodeTerminalOutcome({ ...body, debit: 0.000444 }), TypeError, "money is a string");
});
