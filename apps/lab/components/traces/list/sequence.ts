/**
 * V1 acceptance: "stale requests cannot overwrite current filters."
 *
 * Changing a filter starts a navigation. Two changes in quick succession are two navigations, and
 * they can settle in either order — so the controls must not take their state from whichever
 * response arrives last. This is the whole guard, as a pure reducer: each request takes the next
 * token, the reader's filters move immediately, and a response is applied only when its token is
 * newer than the one already applied. A late or duplicate one is dropped, and `query` — the filters
 * the controls draw — is never rolled back to what an older request asked for.
 *
 * **Arrivals are token-based, not matched by value.** A navigation brings back only a URL, so each
 * issued request records the identity (`key`) its response will carry, and an arrival is resolved to
 * the token it was issued under. Comparing the arrival against the newest query instead cannot tell
 * a late response for an older query apart from a back navigation to it — the `[q2, q1]` order,
 * where the stale arrival was applied because by then nothing looked outstanding.
 *
 * Pure, monotonic, no clock: `tests/v` drives every resolution order directly.
 */

export type IssuedRequest = { token: number; key: string };

/**
 * How many issued requests are remembered. They are not forgotten when they settle: a request whose
 * response is *superseded* must stay recognisable, or a late arrival for it becomes indistinguishable
 * from a back navigation to the same URL and is applied — the bug this module exists to prevent.
 *
 * ponytail: fixed window rather than a full history. Past 16 further requests a very late arrival for
 * an evicted one is read as a fresh navigation; raise this, or put the token in the URL, if that ever
 * matters (it cannot in the router, which drops superseded navigations itself).
 */
export const MAX_TRACKED_REQUESTS = 16;

export type RequestSequence<Q> = {
  /** Tokens handed out so far. */
  issued: number;
  /** The newest token whose response has been applied; nothing older may ever be applied. */
  applied: number;
  /** What the reader has asked for. Moves on `issue`, never on a stale response. */
  query: Q;
  /** The last `MAX_TRACKED_REQUESTS` issued requests, oldest first: token plus response identity. */
  tracked: IssuedRequest[];
};

export function startSequence<Q>(query: Q): RequestSequence<Q> {
  return { issued: 0, applied: 0, query, tracked: [] };
}

/**
 * Take the next token for `query`, whose response will arrive identified by `key`, and show `query`
 * at once (the reader chose it).
 */
export function issue<Q>(
  sequence: RequestSequence<Q>,
  query: Q,
  key: string,
): { sequence: RequestSequence<Q>; token: number } {
  const token = sequence.issued + 1;
  return {
    sequence: {
      issued: token,
      applied: sequence.applied,
      query,
      tracked: [...sequence.tracked, { token, key }].slice(-MAX_TRACKED_REQUESTS),
    },
    token,
  };
}

/**
 * The token an arrival carries: the **newest** request issued under that identity. Two requests for
 * the same query are indistinguishable in their responses, so the newest is the honest reading, and
 * it is the one the reader is waiting for. Null when nothing outstanding was issued under that
 * identity — an arrival this sequence did not cause (a page link, the back button, a fresh load).
 */
export function tokenOf<Q>(sequence: RequestSequence<Q>, key: string): number | null {
  for (let index = sequence.tracked.length - 1; index >= 0; index -= 1) {
    if (sequence.tracked[index].key === key) return sequence.tracked[index].token;
  }
  return null;
}

/**
 * Settle the response carrying `token`. Applied only when the token is the newest issued *and* newer
 * than the one already applied: a response for a request the reader has already moved on from is
 * stale whether it lost the race or arrived twice, and applying it would roll the controls back to
 * filters nobody is asking for any more.
 */
export function settle<Q>(
  sequence: RequestSequence<Q>,
  token: number,
  arrived: Q,
): { sequence: RequestSequence<Q>; applied: boolean } {
  if (token !== sequence.issued || token <= sequence.applied) {
    return { sequence, applied: false };
  }
  return { sequence: { ...sequence, applied: token, query: arrived }, applied: true };
}

/**
 * The same decision for a caller whose response is identified by where it landed rather than by a
 * token of its own — a navigation. An arrival this sequence issued settles under *its* token, so a
 * superseded one is recognised and dropped. An arrival it never issued is an external navigation (a
 * page link, the back button, a fresh load): that is the truth, and it ends the walk.
 */
export function settleArrival<Q>(
  sequence: RequestSequence<Q>,
  arrived: Q,
  key: string,
): { sequence: RequestSequence<Q>; applied: boolean } {
  const token = tokenOf(sequence, key);
  if (token !== null) return settle(sequence, token, arrived);
  return {
    sequence: { issued: sequence.issued, applied: sequence.issued, query: arrived, tracked: sequence.tracked },
    applied: true,
  };
}

/**
 * What the controls draw. While a request the reader made is still settling, they draw that request
 * — not the page that happens to have arrived, which may be answering an earlier one. A caller that
 * is no longer waiting (`waiting: false`) gets the arrival: after a navigation has completed, the
 * URL is the truth even if the response that ended it was never settled by token.
 */
export function displayed<Q>(sequence: RequestSequence<Q>, arrived: Q, waiting: boolean): Q {
  return waiting && sequence.issued > sequence.applied ? sequence.query : arrived;
}
