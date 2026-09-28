// C2 Lab adapter over the content-access RPC's session door (lab-sql's C2-RPC; the schema request
// in the C2 evidence). The database holds every grant, expiry and retention rule and takes the
// user from auth.uid(), so no identity is sent. The handle is minted here and only its SHA-256
// crosses to the database; the content itself is read by the platform's content service
// (infrx.content), never by the Lab. Any bad answer fails closed.
import { createHash, randomBytes } from "node:crypto";

export const ISSUE_RPC = "lab_content_ref_issue";
export const PURPOSES = ["provider_sharing", "external_judging", "training"] as const;
export type Purpose = (typeof PURPOSES)[number];
export type RpcClient = {
  rpc: (name: string, args: Record<string, unknown>) => PromiseLike<{ data: unknown; error: unknown }>;
};
export type Refusal = "not_found" | "forbidden" | "expired" | "unavailable";
export type Issued =
  | { ok: true; handle: string; grantorOrgId: string; requestId: string; expiresAt: string }
  | { ok: false; reason: Refusal };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// 0027's convention: a refusal is "<code>: detail"; anything else is a failed dependency.
const REFUSALS: Record<string, Refusal> = { not_found: "not_found", forbidden: "forbidden", result_expired: "expired" };

export function newHandle(): string {
  return "tc_" + randomBytes(32).toString("base64url");
}

export function handleDigest(handle: string): string {
  return createHash("sha256").update(handle).digest("hex");
}

export function refusalOf(error: unknown): Refusal {
  const message = (error as { message?: unknown } | null)?.message;
  const code = typeof message === "string" ? message.split(":", 1)[0] : "";
  return Object.hasOwn(REFUSALS, code) ? REFUSALS[code] : "unavailable";
}

export async function issueContentRef(
  client: RpcClient,
  input: { providerOrgId: string; grantRef: string; requestId: string; purpose: Purpose },
): Promise<Issued> {
  if (!UUID.test(input.providerOrgId) || !UUID.test(input.requestId)) return { ok: false, reason: "not_found" };
  if (!PURPOSES.includes(input.purpose)) return { ok: false, reason: "forbidden" };
  const handle = newHandle();
  try {
    const { data, error } = await client.rpc(ISSUE_RPC, {
      p_handle_sha256: handleDigest(handle),
      p_provider_org_id: input.providerOrgId,
      p_grant_ref: input.grantRef,
      p_request_id: input.requestId,
      p_purpose: input.purpose,
    });
    if (error) return { ok: false, reason: refusalOf(error) };
    const row = (data ?? {}) as Record<string, unknown>;
    const { grantor_org_id: grantor, request_id: request, expires_at: expires } = row;
    if (typeof grantor !== "string" || !UUID.test(grantor)) return { ok: false, reason: "unavailable" };
    if (request !== input.requestId || typeof expires !== "string" || Number.isNaN(Date.parse(expires))) {
      return { ok: false, reason: "unavailable" };
    }
    return { ok: true, handle, grantorOrgId: grantor, requestId: request, expiresAt: expires };
  } catch {
    return { ok: false, reason: "unavailable" };
  }
}

// CONSOLE-FLOWS: every content state reads as a sentence, so a missing body never breaks a page.
export const CONTENT_COPY: Record<string, string> = {
  available: "",
  metadata_only: "This request was captured without its content.",
  pending: "This request's content is still being processed. Try again shortly.",
  lost: "This request's content could not be stored or read.",
  expired: "This request's content is past its retention or was deleted.",
  off: "Content capture was off for this request.",
};

export function contentMessage(state: string): string {
  return Object.hasOwn(CONTENT_COPY, state) ? CONTENT_COPY[state] : CONTENT_COPY.lost;
}
