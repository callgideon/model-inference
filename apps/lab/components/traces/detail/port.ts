// V2: the two reads the request detail page needs, shaped by what the concurrent lanes built:
// - TraceReadPort = lab-api's GET /lab/v1/traces/{id}?provider_org_id= (WR-V1M-2, codex/w5-lab-api
//   f36a6ab4): developer+ only, the provider's own serving versions only, T3 tombstones applied,
//   metadata only without a current provider_sharing grant. Filed as WR-V2-1 (+ `grant_ref`).
// - ContentPort = C2 (codex/w5-content ae7375e7): a ref bound to grant, recipient and expiry, then the
//   content read through it; any refusal fails closed. Filed as WR-V2-2.
// Until the real adapters are wired both ports are "unavailable" (fail closed); tests drive fake.ts.
import type { Role } from "../../../lib/auth/access.ts";

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
export type ContentRefusal = "not_found" | "forbidden" | "expired" | "unavailable";
export type Content = { text: string; expiresAt: string };

export interface TraceReadPort {
  detail(actor: Actor, requestId: string): Promise<Result<TraceDetail, TraceRefusal>>;
}
export interface ContentPort {
  read(actor: Actor, grantRef: string, requestId: string): Promise<Result<Content, ContentRefusal>>;
}

const down = async () => ({ ok: false, reason: "unavailable" }) as const;
const UNAVAILABLE = { traces: { detail: down }, content: { read: down } };

/** The wiring seam for WR-V2-1/2: the real adapters replace UNAVAILABLE here, never a fake. */
export function tracePorts(): { traces: TraceReadPort; content: ContentPort } {
  return UNAVAILABLE;
}
