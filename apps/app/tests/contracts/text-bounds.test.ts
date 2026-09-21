/**
 * r1 R54: shared character bounds count **Unicode code points**, in both halves.
 *
 * `"".length` counts UTF-16 code units, so one astral character - an emoji, a musical
 * symbol, most of CJK Extension B - counts as two. Every shared bound was therefore
 * *stricter* here than in Python, where `len()` counts code points: a 4000-code-point
 * correction written in emoji was a 400 from the console and an accepted row from the API.
 * Two halves that disagree about the same contract number is precisely what a shared bound
 * exists to prevent.
 *
 * `text_bounds.json` is byte-identical to the Python copy at
 * `apps/infrx-api/infrx/contracts/fixtures/v1/text_bounds.json`, and both halves classify
 * every row the same way. Driven through the *services*, not only through a length
 * helper, so a bound that is checked somewhere this file does not reach still fails here.
 */
import assert from "node:assert/strict";
import test from "node:test";

import boundsJson from "./text_bounds.json" with { type: "json" };
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import {
  MAX_FEEDBACK_TEXT_CHARS,
  MAX_GRANT_REASON_CHARS,
  MAX_IDEMPOTENCY_KEY_CHARS,
  MAX_KEY_NAME_CHARS,
} from "../../lib/contracts/types.ts";
import type { Money } from "../../lib/contracts/money.ts";

type BoundCase = {
  bound: string;
  limit: number;
  case: string;
  text: string;
  code_points: number;
  utf16_units: number;
  within: boolean;
};

const CASES = boundsJson as unknown as BoundCase[];
const LIMITS: Record<string, number> = {
  feedback_text: MAX_FEEDBACK_TEXT_CHARS,
  key_name: MAX_KEY_NAME_CHARS,
  grant_reason: MAX_GRANT_REASON_CHARS,
  idempotency_key: MAX_IDEMPOTENCY_KEY_CHARS,
};

const codePoints = (text: string): number => [...text].length;

test("the shared bound table is the one both halves read", () => {
  assert.ok(CASES.length > 0, "the table must not be empty");
  const astral = CASES.filter((row) => row.utf16_units > row.code_points);
  assert.ok(astral.length > 0, "the table must contain astral cases, or it proves nothing");
  for (const row of CASES) {
    assert.equal(LIMITS[row.bound], row.limit, `${row.bound} limit`);
    // The table's own arithmetic, checked in this language: code points, not units.
    assert.equal(codePoints(row.text), row.code_points, `${row.bound} ${row.case} code points`);
    assert.equal(row.text.length, row.utf16_units, `${row.bound} ${row.case} utf16 units`);
    assert.equal(codePoints(row.text) <= row.limit, row.within, `${row.bound} ${row.case}`);
  }
});

test("every shared bound is enforced in code points by the services that carry it", async () => {
  const services = createFakeConsoleServices();
  const { sessions, ids } = services;
  let key = 0;
  const nextKey = () => `bounds-${(key += 1)}`;

  for (const row of CASES) {
    const where = `${row.bound} / ${row.case}`;
    if (row.bound === "feedback_text") {
      // the submitted value, and the optional comment, both bounded
      const value = await services.feedback.submit(sessions.owner, {
        request_id: ids.availableRequestId,
        name: "correction",
        value: row.text,
        idempotency_key: nextKey(),
      });
      assert.equal(value.ok, row.within, `${where}: feedback value`);
      const comment = await services.feedback.submit(sessions.owner, {
        request_id: ids.availableRequestId,
        name: "thumb",
        value: true,
        comment: row.text,
        idempotency_key: nextKey(),
      });
      assert.equal(comment.ok, row.within, `${where}: feedback comment`);
    } else if (row.bound === "key_name") {
      const created = await services.keys.create(sessions.owner, { name: row.text });
      assert.equal(created.ok, row.within, `${where}: key name`);
    } else if (row.bound === "grant_reason") {
      const granted = await services.adminGrant(sessions.operator, {
        target_org_id: ids.otherOrgId,
        amount: "1.00000000" as Money,
        kind: "promotional",
        reason: row.text,
        idempotency_key: nextKey(),
      });
      assert.equal(granted.ok, row.within, `${where}: grant reason`);
    } else if (row.bound === "idempotency_key") {
      const submitted = await services.feedback.submit(sessions.owner, {
        request_id: ids.availableRequestId,
        name: "thumb",
        value: true,
        idempotency_key: row.text,
      });
      assert.equal(submitted.ok, row.within, `${where}: idempotency key`);
    } else {
      assert.fail(`${where}: no service drives this bound`);
    }
  }
});
