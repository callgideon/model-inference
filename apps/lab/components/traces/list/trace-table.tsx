// V1M list markup (moved from the App's trace table): plain semantic elements (the Lab has no UI kit), a
// list that wraps on a phone rather than a wide table, and native links only, so it works by keyboard.
import type { RejectedParam } from "./query.ts";
import type { ListView } from "./view-model.ts";

const WRAP = { overflowWrap: "anywhere" } as const;

/** Parameters the URL carried and this page refused or ignored, so a wrong link is visible, not silent. */
export function RejectedParams({ rejected, ignored }: { rejected: RejectedParam[]; ignored: string[] }) {
  if (rejected.length === 0 && ignored.length === 0) return null;
  return (
    <div role="status" style={WRAP}>
      {rejected.map((item) => (
        <p key={item.name}>
          <strong>{item.name}</strong> was ignored: {item.why}.
        </p>
      ))}
      {ignored.length > 0 && <p>The request list does not use {ignored.join(", ")}, so it had no effect.</p>}
    </div>
  );
}

export function RequestTable({ view }: { view: ListView }) {
  if (view.kind === "empty") return <p role="status">{view.message}</p>;
  if (view.kind === "error") {
    return (
      <>
        <p role="alert">{view.message}</p>
        {view.firstHref !== null && <a href={view.firstHref}>Newest requests</a>}
      </>
    );
  }
  return (
    <>
      {view.note !== null && <p role="status">{view.note}</p>}
      <ol style={WRAP}>
        {view.rows.map((r) => (
          <li key={r.requestId}>
            <a href={r.href}>{r.requestId}</a>
            <div>
              {r.started} · {r.duration} · {r.model} · {r.content}
            </div>
          </li>
        ))}
      </ol>
      <nav aria-label="Pages">
        {view.firstHref !== null && <a href={view.firstHref}>Newest requests</a>} {view.nextHref !== null && <a href={view.nextHref}>Older requests</a>}
      </nav>
    </>
  );
}
