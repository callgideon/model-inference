/**
 * U2: the Settings page. v1 has no mutable consumer setting: consumer trace capture is off (P-09),
 * `api_keys.trace_mode` is not browser-writable and C3A exposes no settings write. So every privacy
 * row is a fact with its availability stated, never a control that could look saved. Retention
 * periods are the published record's and are read on Docs (A3), not restated here where they could
 * drift from it.
 *
 * Imported by `node --test`: relative `.ts` imports only (R48). Server-only: the type import below
 * keeps it out of client components (tests/c/client-boundary.test.ts).
 */
import type { ConsumerContext } from "../../../lib/services/console.ts";

export type PrivacyRow = {
  title: string;
  status: "Off" | "Not offered" | "Not available" | "Stored for limited periods";
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
      "To run a request we store its content (the request body, the source video, the prepared frames, the result and the streamed output) for limited periods, then delete it. This is not zero data retention. Docs lists each period.",
    href: "/docs#retention",
  },
  {
    title: "Trace capture",
    status: "Off",
    detail:
      "Optional trace capture is off for consumer accounts and cannot be turned on yet. It does not change what we store to run a request (above).",
    href: null,
  },
  {
    title: "Sharing, annotation, evaluation and training",
    status: "Not offered",
    detail:
      "We do not share your request content or use it for annotation, evaluation or training. Signing up grants no permission for any of these, and there is no setting that grants one.",
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
