// node --test "tests/**/*.test.ts"
//
// V1 acceptance: "stale requests cannot overwrite current filters" (oracle CONSOLE-FLOWS).
//
// Two filter changes are two requests, and they can settle in either order. These cases drive the
// out-of-order settle directly, because it is the one thing about this page that cannot be checked
// by looking at it: the bug it prevents — a filter flipping back on its own — needs a slow response
// and a fast one to reproduce by hand.
import assert from "node:assert/strict";
import test from "node:test";

import { parseTraceParams, sameFilters, type FilterState } from "../../app/(console)/traces/query.ts";
import {
  displayed,
  issue,
  settle,
  settleArrival,
  startSequence,
} from "../../app/(console)/traces/sequence.ts";

const NOW = Date.parse("2026-09-21T12:00:00.000Z");

function filters(raw: Record<string, string>): FilterState {
  return parseTraceParams(raw, { now: NOW }).filters;
}

const IDLE = filters({});
const FAILED = filters({ state: "failed" });
const LOST = filters({ content: "lost" });

test("V1-S01 the response to the newest request is applied", () => {
  const first = issue(startSequence(IDLE), FAILED);
  assert.equal(first.token, 1);
  assert.equal(first.sequence.query, FAILED, "the reader's choice shows at once");
  const settled = settle(first.sequence, first.token, FAILED);
  assert.equal(settled.applied, true);
  assert.equal(settled.sequence.applied, 1);
  assert.equal(settled.sequence.query, FAILED);
});

test("V1-S02 a response that lost the race cannot put the older filter back", () => {
  const first = issue(startSequence(IDLE), FAILED);
  const second = issue(first.sequence, LOST);
  assert.equal(second.token, 2);

  // The first request answers last — the classic stale response.
  const late = settle(second.sequence, first.token, FAILED);
  assert.equal(late.applied, false, "an older response is never applied");
  assert.equal(late.sequence.query, LOST, "the current filters survive it");
  assert.equal(late.sequence, second.sequence, "nothing about the sequence moved");

  // The newest one still applies afterwards.
  const current = settle(late.sequence, second.token, LOST);
  assert.equal(current.applied, true);
  assert.equal(current.sequence.query, LOST);
});

test("V1-S03 a duplicate delivery of a response already applied changes nothing", () => {
  const first = issue(startSequence(IDLE), FAILED);
  const applied = settle(first.sequence, first.token, FAILED);
  const again = settle(applied.sequence, first.token, IDLE);
  assert.equal(again.applied, false);
  assert.equal(again.sequence.query, FAILED, "a replayed response cannot rewrite the filters");
  // Nor can a token nobody issued.
  assert.equal(settle(applied.sequence, 99, IDLE).applied, false);
  assert.equal(settle(applied.sequence, 0, IDLE).applied, false);
  assert.equal(settle(applied.sequence, -1, IDLE).applied, false);
});

test("V1-S04 an arrival with nothing outstanding is the truth, whoever navigated", () => {
  // A page link or the back button: the reader made no filter change, so the URL wins.
  const idle = startSequence(IDLE);
  const arrival = settleArrival(idle, LOST, sameFilters);
  assert.equal(arrival.applied, true);
  assert.equal(arrival.sequence.query, LOST);
});

test("V1-S05 an arrival that does not answer the outstanding request is dropped", () => {
  const outstanding = issue(startSequence(IDLE), LOST).sequence;
  const stale = settleArrival(outstanding, FAILED, sameFilters);
  assert.equal(stale.applied, false);
  assert.equal(stale.sequence.query, LOST, "the outstanding request still owns the controls");

  const answer = settleArrival(outstanding, LOST, sameFilters);
  assert.equal(answer.applied, true);
  assert.equal(answer.sequence.applied, answer.sequence.issued, "the walk is settled");

  // A page of the same filters but a different position in the walk is a different request.
  const paged: FilterState = { ...LOST, cursor: "bmV4dA==", pinned: true };
  assert.equal(settleArrival(outstanding, paged, sameFilters).applied, false);
});

test("V1-S06 the controls draw the newest request while one is outstanding", () => {
  const outstanding = issue(startSequence(IDLE), LOST).sequence;
  // A page arrives mid-flight answering the *previous* filters; the controls keep the new ones.
  assert.equal(displayed(outstanding, FAILED, true), LOST);
  // Once nothing is outstanding, the URL is what the controls draw.
  const settled = settleArrival(outstanding, LOST, sameFilters).sequence;
  assert.equal(displayed(settled, FAILED, false), FAILED);
  assert.equal(displayed(settled, FAILED, true), FAILED, "settled is settled, pending or not");
  assert.equal(displayed(startSequence(IDLE), FAILED, true), FAILED, "nothing issued yet");
});
