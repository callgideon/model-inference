/**
 * U2: the Settings page's facts. Every privacy row is a fact with its availability stated, never a
 * control. The one mutable setting - per-key trace capture and the withdrawal of a provider grant -
 * is C-07's data-use section (`data-use.tsx`), shown from the API's record and only when it answers;
 * these facts hold whatever that record says (capture is off until the owner turns it on for a key,
 * D3; signup grants nothing). Retention periods are the published record's and are read on Docs
 * (A3), not restated here where they could drift from it.
 *
 * Imported by `node --test`: relative `.ts` imports only (R48). Server-only: the type import below
 * keeps it out of client components (tests/c/client-boundary.test.ts).
 */
import type { ConsumerContext } from "../../../lib/services/console.ts";

export type PrivacyRow = {
  title: string;
  status: "Off unless you turn it on" | "Off unless you grant it" | "Not available" | "Stored for limited periods";
  detail: string;
  href: string | null;
};

export type SettingsModel = {
  account:
    | { kind: "ready"; email: string; status: "Verified" | "Not verified" | "Verified, finishing setup"; suspended: boolean }
    | { kind: "unavailable"; message: string };
  privacy: PrivacyRow[];
};

/** Where a consumer asks for what the console cannot do (the address the sign-in page publishes). */
export const SUPPORT_EMAIL = "hello@callbill.ai";

const PRIVACY: PrivacyRow[] = [
  {
    title: "Serving retention",
    status: "Stored for limited periods",
    detail:
      "To run a request we store its content (the request body, the source video, the prepared frames, the result and the streamed output) for limited periods, after which it can no longer be read. This is not zero data retention. Docs lists each period.",
    href: "/docs#retention",
  },
  {
    title: "Trace capture",
    status: "Off unless you turn it on",
    detail:
      "Optional trace capture records a key's requests only after the account owner turns it on for that key below. It does not change what we store to run a request (above).",
    href: null,
  },
  {
    title: "Sharing, annotation, evaluation and training",
    status: "Off unless you grant it",
    detail:
      "We do not share your request content or use it for annotation, evaluation or training unless the account owner grants it to a named provider. Signing up grants no permission for any of these, and the console does not create one; any grant you made is listed below, where you can withdraw it.",
    href: null,
  },
  {
    title: "Data export and account deletion",
    status: "Not available",
    detail: `Not available in the console yet. Email ${SUPPORT_EMAIL} to ask for either.`,
    href: `mailto:${SUPPORT_EMAIL}`,
  },
];

export function settingsModel(context: ConsumerContext): SettingsModel {
  const privacy = PRIVACY.map((row) => ({ ...row }));
  switch (context.state) {
    case "ready":
      return { account: { kind: "ready", email: context.account.email, status: "Verified", suspended: context.account.suspended }, privacy };
    case "unverified":
      return { account: { kind: "ready", email: context.email, status: "Not verified", suspended: false }, privacy };
    case "onboarding":
      return { account: { kind: "ready", email: context.email, status: "Verified, finishing setup", suspended: false }, privacy };
  }
  return { account: { kind: "unavailable", message: "Your account could not be loaded right now. Reload the page to try again." }, privacy };
}
