// One request's panels on the Lab primitives (components/ui). Text is rendered as text, never as HTML:
// a captured body is a React text child inside <pre>, so markup in it is shown, not run.
import { Fragment } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { buttonClass } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { ServiceState } from "@/components/ui/service-state";
import type { ReviewResult } from "../../../lib/services/review/index.ts";
import { listHref } from "../list/query.ts";
import { NO_FILTER, type TraceDetail, type TraceRefusal } from "./port.ts";
import { access, contentRegion, feedbackView, metadataRows, PARTIAL_COPY, TRACE_COPY, TRACE_TITLE } from "./view.ts";
import styles from "../list/requests.module.css";

/** The trace read refused: its own state; only an unavailable read offers a retry (a full re-read). */
export function TraceRefused({ reason, retryHref }: { reason: TraceRefusal; retryHref: string }) {
  const action =
    reason === "unavailable" ? (
      <a href={retryHref} className={buttonClass("secondary")}>
        Try again
      </a>
    ) : (
      <Link href="/requests">Back to requests</Link>
    );
  return <ServiceState state={reason} title={TRACE_TITLE[reason]} explanation={TRACE_COPY[reason]} action={action} />;
}

function Id({ value, label }: { value: string; label: string }) {
  return (
    <>
      <code className="lab-id">{value}</code> <CopyButton value={value} label={label} />
    </>
  );
}

/** Identity first (request, model, serving version), then the record's own fields. */
export function MetadataPanel({ detail }: { detail: TraceDetail }) {
  const state = access(detail);
  return (
    <section aria-labelledby="request-metadata" className="lab-stack">
      <h2 id="request-metadata">Summary</h2>
      <dl className={styles.summary}>
        <dt>Request</dt>
        <dd>
          <Id value={detail.request_id} label="Copy request id" />
        </dd>
        <dt>Model</dt>
        <dd>
          <Link href={listHref(null, { ...NO_FILTER, model_id: detail.model_id })}>{detail.model_id}</Link>
        </dd>
        <dt>Serving version</dt>
        <dd>
          <Id value={detail.serving_version_id} label="Copy serving version id" />{" "}
          <Link href={listHref(null, { ...NO_FILTER, serving_version_id: detail.serving_version_id })}>Requests on this serving version</Link>
        </dd>
        <dt>Content access</dt>
        <dd>
          <Badge tone={state.tone}>{state.label}</Badge>
        </dd>
        {metadataRows(detail).map(([term, value]) => (
          <Fragment key={term}>
            <dt>{term}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
      </dl>
    </section>
  );
}

export function ContentPanel({ detail }: { detail: TraceDetail }) {
  const region = contentRegion(detail);
  return (
    <section aria-labelledby="request-content" className="lab-stack">
      <h2 id="request-content">Content</h2>
      {region.kind === "none" ? (
        <p role="status">{region.copy}</p>
      ) : (
        <>
          {region.partial && (
            <p role="status">
              <Badge tone="warning">Partial capture</Badge> {PARTIAL_COPY}
            </p>
          )}
          <dl className={styles.summary}>
            {region.provenance.map(([term, value]) => (
              <Fragment key={term}>
                <dt>{term}</dt>
                <dd className="lab-id">{value}</dd>
              </Fragment>
            ))}
          </dl>
          <pre className={styles.content}>{region.text}</pre>
        </>
      )}
    </section>
  );
}

export function FeedbackPanel({ result }: { result: ReviewResult }) {
  const view = feedbackView(result);
  return (
    <section aria-labelledby="request-feedback" className="lab-stack">
      <h2 id="request-feedback">Feedback</h2>
      <p>{view.note}</p>
      {view.empty !== null ? (
        <p role="status">{view.empty}</p>
      ) : (
        <ul>
          {view.rows.map((r) => (
            <li key={r.id} className="lab-id" style={{ whiteSpace: "pre-wrap" }}>
              <strong>{r.what}</strong> · {r.who} · {r.when}
              {r.text !== null && <div>{r.text}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
