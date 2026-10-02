// node --test "tests/**/*.test.ts"
//
// UX-07 (04-consumer.md C-04/C-05/C-07, UX-T05): the consumer's usage, request detail, credits and
// settings presentation over the existing view models (no new read, no new action). Failure oracles: a
// result note or privacy row that claims physical deletion (WR-UXF-4/5), a detail that hides a state
// or shows content for a request the store would not serve, a retry that submits inference, a wallet
// failure or an unreadable charge rendered as a zero, a tiny debit read as free, execution and money
// states merged into one label, and a hold called spent.
import assert from "node:assert/strict";
import test from "node:test";

import { defaultCreditFixture } from "../../../app/(console)/billing/credit-fixture.ts";
import type { ConsumerJob } from "../../../app/(console)/billing/credit-reads.ts";
import { requestDetailModel } from "../../../app/(console)/usage/[requestId]/request-view-model.ts";
import { settingsModel } from "../../../app/(console)/settings/view-model.ts";

const job = (over: Partial<ConsumerJob> = {}): ConsumerJob => ({ ...defaultCreditFixture().jobs[0], ...over });
const DELETION = /\b(remov|delet|purg|eras|destroy)/i;

test("UXU-01 result notes say when the result stops being readable, never that content is removed or deleted", () => {
  const available = requestDetailModel({ ok: true, value: job({ resultAvailable: true }) });
  const expired = requestDetailModel({ ok: true, value: job({ resultAvailable: false }) });
  assert.ok(available.kind === "ready" && expired.kind === "ready");
  assert.equal(available.value.result.access, "available");
  assert.equal(
    available.value.result.note,
    "Readable until 2026-09-20 12:10 UTC. After that the result is no longer available; this page keeps the request's details and charge.",
  );
  assert.equal(expired.value.result.access, "expired");
  assert.equal(
    expired.value.result.note,
    "The result stopped being available at 2026-09-20 12:10 UTC. Request status and usage remain available.",
  );
  for (const over of [{}, { resultAvailable: false }, { state: "running" }, { state: "failed" }, { resultExpiresAt: null }, { settlementState: "held_unknown" }]) {
    const model = requestDetailModel({ ok: true, value: job(over) });
    assert.ok(model.kind === "ready");
    assert.doesNotMatch(model.value.result.note, DELETION, JSON.stringify(over));
  }
});

test("UXU-02 Settings' serving retention says stored content stops being readable, with no deletion promise", () => {
  const retention = settingsModel({ state: "ready", account: { email: "a@example.com", suspended: false } } as never).privacy.find((r) =>
    /retention/i.test(r.title),
  );
  assert.ok(retention);
  assert.match(retention.detail, /for limited periods, after which it can no longer be read/);
  for (const row of settingsModel({ state: "unavailable" } as never).privacy) {
    assert.doesNotMatch(row.detail, /then delete|is deleted|deletes it/i, row.title);
  }
});
