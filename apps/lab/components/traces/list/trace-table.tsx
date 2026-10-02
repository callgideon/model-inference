// The request list's markup on the Lab primitives (components/ui, frozen by UX-00): a table that scrolls
// inside its own frame (the page never scrolls sideways), native links only, states as ServiceState.
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { buttonClass } from "@/components/ui/button";
import { ServiceState } from "@/components/ui/service-state";
import type { TraceFilter } from "../detail/port.ts";
import type { RejectedParam } from "./query.ts";
import type { ListView } from "./view-model.ts";
import styles from "./requests.module.css";

/** Parameters the URL carried and this page refused or ignored, so a wrong link is visible, not silent. */
export function RejectedParams({ rejected, ignored }: { rejected: RejectedParam[]; ignored: string[] }) {
  if (rejected.length === 0 && ignored.length === 0) return null;
  return (
    <div role="status" className="lab-id">
      {rejected.map((item) => (
        <p key={item.name}>
          <strong>{item.name}</strong> was ignored: {item.why}.
        </p>
      ))}
      {ignored.length > 0 && <p>The request list does not use {ignored.join(", ")}, so it had no effect.</p>}
    </div>
  );
}

/** The active server-side filter and the way back to every request. */
export function FilterNote({ filter }: { filter: TraceFilter }) {
  const parts = [filter.model_id !== null && `model ${filter.model_id}`, filter.serving_version_id !== null && `serving version ${filter.serving_version_id}`].filter(Boolean);
  if (parts.length === 0) return null;
  return (
    <p role="status" className="lab-id">
      Showing requests for {parts.join(" and ")}. <Link href="/requests">Show all requests</Link>
    </p>
  );
}

export function RequestTable({ view }: { view: ListView }) {
  if (view.kind === "empty") {
    return (
      <ServiceState
        state="empty"
        title={view.title}
        explanation={view.message}
        action={view.clearHref !== null ? <Link href={view.clearHref}>Show all requests</Link> : undefined}
      />
    );
  }
  if (view.kind === "error") {
    return (
      <ServiceState
        state={view.state}
        title={view.title}
        explanation={view.message}
        action={
          view.state === "unavailable" ? (
            <a href={view.retryHref} className={buttonClass("secondary")}>
              Try again
            </a>
          ) : undefined
        }
      />
    );
  }
  return (
    <>
      {view.note !== null && <p role="status">{view.note}</p>}
      {view.rows.length > 0 && (
        <div className={styles.frame}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Request</th>
                <th scope="col">Started</th>
                <th scope="col">Elapsed</th>
                <th scope="col">Model / revision</th>
                <th scope="col">Capture</th>
                <th scope="col">Content access</th>
              </tr>
            </thead>
            <tbody>
              {view.rows.map((r) => (
                <tr key={r.requestId}>
                  <td className="lab-id">
                    <Link href={r.href}>{r.requestId}</Link>
                  </td>
                  <td>
                    <time dateTime={r.startedAt}>{r.started}</time>
                  </td>
                  <td>{r.elapsed}</td>
                  <td className="lab-id">
                    <Link href={r.modelHref}>{r.model}</Link>
                    <div className={styles.muted}>{r.revision}</div>
                  </td>
                  <td>{r.mode}</td>
                  <td>
                    <Badge tone={r.access.tone}>{r.access.label}</Badge>
                    {r.loss !== null && <div className={styles.muted}>Lost: {r.loss}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <nav aria-label="Pages" className={styles.pager}>
        {view.firstHref !== null && (
          <Link href={view.firstHref} className={buttonClass("secondary", "sm")}>
            Newest requests
          </Link>
        )}
        {view.nextHref !== null && (
          <Link href={view.nextHref} className={buttonClass("secondary", "sm")}>
            Older requests
          </Link>
        )}
      </nav>
    </>
  );
}
