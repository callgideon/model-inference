/**
 * What a route error boundary says, and which recovery it must wire up (U1).
 *
 * The copy lives here rather than in the two `error.tsx` files so it can be tested: a boundary is a
 * `.tsx` file, which `node --test` cannot load (R48), and the wording is the part worth pinning.
 *
 * **The recovery is `retry`, not `reset`.** Next 16.3.5's shipped documentation
 * (`node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/error.md`) states that
 * `retry()` "will try to re-fetch and re-render the error boundary's children", while `reset()`
 * re-renders them "without re-fetching the contents" and "in most cases, you should use `retry()`
 * instead". Both of these pages are Server Components, so every throw the boundary catches happened
 * on the server: `reset()` re-renders the same errored payload and the button can never recover.
 */

/** The prop each `error.tsx` must destructure and call. Asserted against the files themselves. */
export const BOUNDARY_RECOVERY_PROP = "retry";

export type BoundaryScope = "usage" | "balance";

export type BoundaryCopy = {
  headline: string;
  /** Why the reader is not out of pocket. Never the thrown message: it can carry internals. */
  detail: string;
  action: string;
};

const UNAFFECTED: Record<BoundaryScope, string> = {
  usage: "Your requests and your balance are unaffected — nothing here changes accounting.",
  balance: "Your ledger and your grants are unaffected — nothing here changes accounting.",
};

export function boundaryCopy(scope: BoundaryScope): BoundaryCopy {
  const what = scope === "usage" ? "your usage" : "your balance";
  return {
    headline: "This page could not be displayed",
    detail: `Something went wrong while rendering ${what}. ${
      Object.hasOwn(UNAFFECTED, scope) ? UNAFFECTED[scope] : UNAFFECTED.usage
    }`,
    action: "Try again",
  };
}
