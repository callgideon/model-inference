// The reads the request pages need, over lab-api's provider trace read (gateway/routes/lab_traces.py,
// AP-07c): developer+ only, the provider's own serving versions only, T3 tombstones applied, metadata
// only without a current provider_sharing grant. Each item carries `access_state` (why content is or is
// not there, the route's ladder), `elapsed_ms` and its pins; a granted detail carries its content inline
// (`content`, text, null when unreadable) - there is no separate content read. The list's filters are
// the route's (`serving_version_id`, `model_id`), applied server-side and bound into its cursor.
import type { Actor } from "../../../lib/auth/access.ts";
import { labTraces } from "../../../lib/services/traces/server.ts";
import type { TracePage } from "../../../lib/services/traces/port.ts";

/** Always the session's workspace (lib/auth/guard.ts), never a form or URL value: lib/auth/access.ts's one Actor (LAB-08). */
export type { Actor };
/** lab_traces.py `access_state`, one reason each, first match wins. */
export type AccessState = "content" | "partial" | "metadata" | "revoked" | "expired" | "not_captured";
type Metadata = {
  request_id: string;
  started_at: string;
  completed_at: string | null;
  mode: string; // off | minimal | full
  loss_reason: string; // "none" unless the capture was lost
  serving_version_id: string;
  model_revision: string;
  rate_card_version: string | null;
  policy_version: string | null;
  price_version: string;
  request_schema_version: number;
  model_id: string;
  access_state: AccessState;
  elapsed_ms: number | null; // admission to capture finish; null when unfinished
};
export type TraceDetail =
  | (Metadata & { access: "metadata" })
  | (Metadata & {
      access: "content";
      grantor_org_id: string;
      grant_ref: string;
      content_complete: boolean;
      content_bytes: number;
      content_available: boolean;
      content?: string | null; // the detail only
    });
export type Result<T, R extends string> = { ok: true; value: T } | { ok: false; reason: R };
export type TraceRefusal = "denied" | "not_found" | "unavailable";
/** The route's list filters; null = not filtered. */
export type TraceFilter = { model_id: string | null; serving_version_id: string | null };
export const NO_FILTER: TraceFilter = { model_id: null, serving_version_id: null };

export interface TraceReadPort {
  detail(actor: Actor, requestId: string): Promise<Result<TraceDetail, TraceRefusal>>;
}
export interface TracePort extends TraceReadPort {
  list(actor: Actor, cursor: string | null, filter: TraceFilter): Promise<Result<TracePage, TraceRefusal>>;
}
/** The wiring seam for the trace reads: the real adapter, never a fake. */
export function tracePorts(): { traces: TracePort } {
  return { traces: labTraces() };
}
