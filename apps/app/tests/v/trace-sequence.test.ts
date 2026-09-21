// node --test "tests/**/*.test.ts"
//
// V1 acceptance: "stale requests cannot overwrite current filters" (oracle CONSOLE-FLOWS).
//
// Two filter changes are two requests, and they can settle in either order. These cases drive the
// out-of-order settle directly, because it is the one thing about this page that cannot be checked
// by looking at it: the bug it prevents — a filter flipping back on its own — needs a slow response
// and a fast one to reproduce by hand. The last case is exhaustive over resolution orders, including
// the ones where some requests never resolve at all.
import assert from "node:assert/strict";
import test from "node:test";

import { parseTraceParams, traceHref, type FilterState } from "../../app/(console)/traces/query.ts";
import {
  MAX_TRACKED_REQUESTS,
  displayed,
  issue,
  settle,
  settleArrival,
  startSequence,
  tokenOf,
} from "../../app/(console)/traces/sequence.ts";

const NOW = Date.parse("2026-09-21T12:00:00.000Z");

function filters(raw: Record<string, string>): FilterState {
  return parseTraceParams(raw, { now: NOW, keyIds: [] }).filters;
}

/** The identity a response arrives under: where the navigation landed. */
function key(state: FilterState): string {
  return traceHref(state);
}

const IDLE = filters({});
const FAILED = filters({ state: "failed" });
const LOST = filters({ content: "lost" });

test("V1-S01 the response to the newest request is applied", () => {
  const first = issue(startSequence(IDLE), FAILED, key(FAILED));
  assert.equal(first.token, 1);
  assert.equal(first.sequence.query, FAILED, "the reader's choice shows at once");
  const settled = settle(first.sequence, first.token, FAILED);
  assert.equal(settled.applied, true);
  assert.equal(settled.sequence.applied, 1);
  assert.equal(settled.sequence.query, FAILED);
});

test("V1-S02 a response that lost the race cannot put the older filter back", () => {
  const first = issue(startSequence(IDLE), FAILED, key(FAILED));
  const second = issue(first.sequence, LOST, key(LOST));
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
  const first = issue(startSequence(IDLE), FAILED, key(FAILED));
  const applied = settle(first.sequence, first.token, FAILED);
  const again = settle(applied.sequence, first.token, IDLE);
  assert.equal(again.applied, false);
  assert.equal(again.sequence.query, FAILED, "a replayed response cannot rewrite the filters");
  for (const token of [99, 0, -1]) {
    assert.equal(settle(applied.sequence, token, IDLE).applied, false, `token ${token}`);
  }
});

test("V1-S04 an arrival nobody issued is an external navigation, and is the truth", () => {
  // A page link, a pasted URL or the back button, with nothing in flight.
  const arrival = settleArrival(startSequence(IDLE), LOST, key(LOST));
  assert.equal(arrival.applied, true);
  assert.equal(arrival.sequence.query, LOST);
});

test("V1-S05 an arrival is resolved by the token it was issued under, not by its value", () => {
  const first = issue(startSequence(IDLE), FAILED, key(FAILED));
  const second = issue(first.sequence, LOST, key(LOST));
  assert.equal(tokenOf(second.sequence, key(FAILED)), 1);
  assert.equal(tokenOf(second.sequence, key(LOST)), 2);
  assert.equal(tokenOf(second.sequence, key(IDLE)), null, "never issued");

  // The order value-matching gets wrong: the newest arrives first, then the superseded one. Once the
  // newest has settled nothing is in flight, so a stale arrival looks exactly like a back navigation
  // to that URL — unless the request that minted it is still recognisable.
  const settled = settleArrival(second.sequence, LOST, key(LOST));
  assert.equal(settled.applied, true);
  const stale = settleArrival(settled.sequence, FAILED, key(FAILED));
  assert.equal(stale.applied, false, "the superseded request's response is still recognised");
  assert.equal(stale.sequence.query, LOST, "and cannot put its filters back");

  // The same query asked for twice resolves to the newer token: the one the reader is waiting for.
  const again = issue(settled.sequence, FAILED, key(FAILED));
  assert.equal(tokenOf(again.sequence, key(FAILED)), again.token);
  assert.equal(settleArrival(again.sequence, FAILED, key(FAILED)).applied, true);

  // The tracking window is bounded, and the bound is the documented ceiling.
  let many = startSequence(IDLE);
  for (let index = 0; index < MAX_TRACKED_REQUESTS + 4; index += 1) {
    many = issue(many, FAILED, `/traces?probe=${index}`).sequence;
  }
  assert.equal(many.tracked.length, MAX_TRACKED_REQUESTS);
  assert.equal(tokenOf(many, "/traces?probe=0"), null, "evicted, per the documented ceiling");
  assert.equal(tokenOf(many, `/traces?probe=${MAX_TRACKED_REQUESTS + 3}`), many.issued);
});

test("V1-S06 the controls draw the newest request only while one is settling", () => {
  const outstanding = issue(startSequence(IDLE), LOST, key(LOST)).sequence;
  // A page arrives mid-flight answering the *previous* filters; the controls keep the new ones.
  assert.equal(displayed(outstanding, FAILED, true), LOST);
  // Not waiting any more — a completed navigation, or the back button — and the URL is the truth.
  // Reading the sequence instead leaves the controls stuck on a pick the reader has navigated away
  // from, which is the normal state after every back navigation.
  assert.equal(displayed(outstanding, FAILED, false), FAILED);
  const settled = settleArrival(outstanding, LOST, key(LOST)).sequence;
  assert.equal(displayed(settled, FAILED, false), FAILED);
  assert.equal(displayed(settled, FAILED, true), FAILED, "settled is settled, waiting or not");
  assert.equal(displayed(startSequence(IDLE), FAILED, true), FAILED, "nothing issued yet");
});

test("V1-S07 no resolution order of up to four requests can show an older filter", () => {
  /** Every ordering of every subset, including the empty one: some requests never resolve. */
  function orders(tokens: number[]): number[][] {
    const found: number[][] = [[]];
    for (const token of tokens) {
      for (const rest of orders(tokens.filter((other) => other !== token))) {
        found.push([token, ...rest]);
      }
    }
    return found;
  }

  let steps = 0;
  for (let count = 1; count <= 4; count += 1) {
    const queries = Array.from({ length: count }, (unused, index) =>
      filters({ size: "50", model: `m${index}` }),
    );
    const newest = queries[count - 1];

    for (const order of orders(queries.map((unused, index) => index + 1))) {
      // A mixed run: one arrival is an error page, which lands at the same URL — so a stale error
      // must be dropped exactly like a stale page of rows.
      for (const errorAt of [-1, 0, Math.max(order.length - 1, 0)]) {
        let sequence = startSequence(IDLE);
        for (const query of queries) sequence = issue(sequence, query, key(query)).sequence;
        assert.equal(sequence.query, newest, "the newest request is what the controls draw");

        let applied = 0;
        for (const [step, token] of order.entries()) {
          const before = sequence;
          const arrived = queries[token - 1];
          const isError = step === errorAt;
          const result = settleArrival(sequence, arrived, key(arrived));
          sequence = result.sequence;

          if (token === count && applied === 0) {
            assert.equal(result.applied, true, `token ${token} answers the newest request`);
            applied = token;
          } else {
            assert.equal(
              result.applied,
              false,
              `order ${order.join(">")}: token ${token} is stale${isError ? " (error page)" : ""}`,
            );
            assert.deepEqual(sequence, before, "a dropped arrival changes nothing at all");
          }
          assert.equal(sequence.applied, applied, "applied only ever moves to the newest token");
          assert.equal(sequence.query, newest, "the filters are always the newest asked for");
          assert.ok(sequence.applied <= sequence.issued);
          if (applied === 0) {
            assert.equal(displayed(sequence, queries[0], true), newest, "still settling");
          }
          steps += 1;
        }
      }
    }
  }
  assert.ok(steps > 200, `expected an exhaustive sweep, replayed ${steps} arrivals`);
});
