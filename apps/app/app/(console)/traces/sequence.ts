/**
 * V1 acceptance: "stale requests cannot overwrite current filters."
 *
 * Changing a filter starts a navigation. Two changes in quick succession are two navigations, and
 * they can settle in either order — so the controls must not take their state from whichever
 * response arrives last. This is the whole guard, as a pure reducer: each request takes the next
 * token, the reader's filters move immediately, and a response is applied only when it answers the
 * newest token. A late or duplicate one is dropped, and `query` — the filters the controls draw — is
 * never rolled back to what an older request asked for.
 *
 * Pure, monotonic, no clock: `tests/v` drives it directly with out-of-order settles.
 */

export type RequestSequence<Q> = {
  /** Tokens handed out so far; the newest is the only one whose response may be applied. */
  issued: number;
  /** The newest token whose response has been applied. */
  applied: number;
  /** What the reader has asked for. Moves on `issue`, never on a stale response. */
  query: Q;
};

export function startSequence<Q>(query: Q): RequestSequence<Q> {
  return { issued: 0, applied: 0, query };
}

/** Take the next token for `query`, and show `query` at once (the reader chose it). */
export function issue<Q>(
  sequence: RequestSequence<Q>,
  query: Q,
): { sequence: RequestSequence<Q>; token: number } {
  const token = sequence.issued + 1;
  return { sequence: { issued: token, applied: sequence.applied, query }, token };
}

/**
 * Settle the response carrying `token`. Applied only when the token is the newest issued and newer
 * than whatever is already applied — so a response that overtook a later one, and a duplicate
 * delivery of one already applied, both change nothing.
 */
export function settle<Q>(
  sequence: RequestSequence<Q>,
  token: number,
  arrived: Q,
): { sequence: RequestSequence<Q>; applied: boolean } {
  if (token !== sequence.issued || token <= sequence.applied) {
    return { sequence, applied: false };
  }
  return { sequence: { issued: sequence.issued, applied: token, query: arrived }, applied: true };
}

/**
 * The same decision for a caller whose response carries no token of its own — a navigation, where
 * all that comes back is the URL. With nothing outstanding the arrival is simply the truth (a page
 * link, the back button); with a request outstanding it is applied only if it is the one that
 * request asked for, and any other arrival is the stale response this module exists to drop.
 */
export function settleArrival<Q>(
  sequence: RequestSequence<Q>,
  arrived: Q,
  same: (a: Q, b: Q) => boolean,
): { sequence: RequestSequence<Q>; applied: boolean } {
  if (sequence.issued === sequence.applied) {
    return { sequence: { ...sequence, query: arrived }, applied: true };
  }
  if (!same(sequence.query, arrived)) return { sequence, applied: false };
  return settle(sequence, sequence.issued, arrived);
}

/**
 * What the controls draw. While a request the reader made is still settling, they draw that request
 * — not the page that happens to have arrived, which may be answering an earlier one.
 */
export function displayed<Q>(sequence: RequestSequence<Q>, arrived: Q, outstanding: boolean): Q {
  return outstanding && sequence.issued > sequence.applied ? sequence.query : arrived;
}
