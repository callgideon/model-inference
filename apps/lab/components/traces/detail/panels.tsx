// V2 panels: plain semantic elements (the Lab has no UI kit) that stack on a phone and work from the
// keyboard with native links only. Text is rendered as text, never as HTML.
import type { ReviewResult } from "../../../lib/services/review/index.ts";
import type { TraceDetail } from "./port.ts";
import { CONTENT_COPY, contentState, feedbackView, metadataRows } from "./view.ts";

const WRAP = { whiteSpace: "pre-wrap", overflowWrap: "anywhere" } as const;

export function MetadataPanel({ detail }: { detail: TraceDetail }) {
  return (
    <section aria-labelledby="request-metadata">
      <h2 id="request-metadata">Request</h2>
      <dl style={{ overflowWrap: "anywhere" }}>
        {metadataRows(detail).map(([term, value]) => (
          <div key={term}>
            <dt>{term}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function ContentPanel({ detail }: { detail: TraceDetail }) {
  return (
    <section aria-labelledby="request-content">
      <h2 id="request-content">Content</h2>
      <p role="status">{CONTENT_COPY[contentState(detail)]}</p>
    </section>
  );
}

export function FeedbackPanel({ result }: { result: ReviewResult }) {
  const view = feedbackView(result);
  return (
    <section aria-labelledby="request-feedback">
      <h2 id="request-feedback">Feedback</h2>
      <p>{view.note}</p>
      {view.empty !== null ? (
        <p role="status">{view.empty}</p>
      ) : (
        <ul>
          {view.rows.map((r) => (
            <li key={r.id} style={WRAP}>
              <strong>{r.what}</strong> · {r.who} · {r.when}
              {r.text !== null && <div>{r.text}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
