import type { ReactNode } from "react";
import { CircleSlash, Clock, Inbox, LoaderCircle, Lock, SearchX } from "lucide-react";

/** The fixture-state names every lane uses (02-foundations ServiceState). */
export const SERVICE_STATES = ["loading", "empty", "unavailable", "denied", "not_found", "stale"] as const;
export type ServiceStateKind = (typeof SERVICE_STATES)[number];

const ICONS = { loading: LoaderCircle, empty: Inbox, unavailable: CircleSlash, denied: Lock, not_found: SearchX, stale: Clock };

/**
 * A section that has no records to show, and why. A transport failure is `unavailable`, never
 * `empty`; `stale` labels the last successful observation. `unavailable` is announced as an alert,
 * the rest as status. `explanation` is safe copy (no raw server text); `diagnosticId` is optional.
 */
export function ServiceState({
  state,
  title,
  explanation,
  action,
  diagnosticId,
}: {
  state: ServiceStateKind;
  title: string;
  explanation: ReactNode;
  action?: ReactNode;
  diagnosticId?: string;
}) {
  const Icon = ICONS[state];
  return (
    <section
      className={`lab-state lab-state--${state}`}
      data-state={state}
      role={state === "unavailable" ? "alert" : "status"}
      aria-busy={state === "loading" || undefined}
    >
      <Icon aria-hidden className={state === "loading" ? "lab-spin" : undefined} />
      <h2 className="lab-state__title">{title}</h2>
      <p className="lab-state__explanation">{explanation}</p>
      {action ? <div>{action}</div> : null}
      {diagnosticId ? (
        <p className="lab-state__diagnostic">
          Diagnostic ID <code>{diagnosticId}</code>
        </p>
      ) : null}
    </section>
  );
}
