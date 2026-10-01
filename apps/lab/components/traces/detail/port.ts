// V2: the read the request detail page needs. TraceReadPort = lab-api's GET
// /lab/v1/traces/{id}?provider_org_id= (WR-V1M-2 / WR-V2-1, wired as labTraces()): developer+ only, the
// provider's own serving versions only, T3 tombstones applied, metadata only without a current
// provider_sharing grant. Content reads through C2 (WR-V2-2) are not wired: there is no content port, and
// the page states each record's content state instead (W6 LAB-06; the item stays in the carried-work register).
import type { Role } from "../../../lib/auth/access.ts";
import { labTraces } from "../../../lib/services/traces/server.ts";

/** Always the session's workspace (lib/auth/guard.ts), never a form or URL value. */
export type Actor = { providerId: string; role: Role };
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
  model_id: string;
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
    });
export type Result<T, R extends string> = { ok: true; value: T } | { ok: false; reason: R };
export type TraceRefusal = "denied" | "not_found" | "unavailable";

export interface TraceReadPort {
  detail(actor: Actor, requestId: string): Promise<Result<TraceDetail, TraceRefusal>>;
}
/** The wiring seam for the trace read: the real adapter, never a fake. */
export function tracePorts(): { traces: TraceReadPort } {
  return { traces: labTraces() };
}
