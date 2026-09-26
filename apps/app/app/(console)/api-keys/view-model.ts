/**
 * U2: every state the API Keys page and its dialogs render, decided from C0's consumer context and
 * `reads.keys()` result and from C3A's action results. Pure, and client-safe: it imports only
 * contract types, so the create dialog can use it without pulling `lib/services` into a bundle.
 *
 * Imported by `node --test`: relative `.ts` imports only (R48).
 */
import type { ApiKeyCreated, ApiKeySummary, Result } from "../../../lib/contracts/types.ts";

/** P-26 (15-pending-inputs.md), verbatim: one copy, the sentence A3 publishes on Docs (WR-U2-3). */
export { REVOCATION_COPY } from "../docs/content.ts";

export const ONE_TIME_COPY =
  "This is the only time this key is shown. We store a hash of it, not the key, so it cannot be shown again. Keep it in a secret manager or an environment variable.";

export const LOST_KEY_COPY =
  "Lost a key? It cannot be recovered or shown again. Create a new key, switch your application to it, then revoke the old one.";

export const REPLAYED_COPY =
  "This key was already created by an earlier attempt and its secret cannot be shown again. Revoke it and create a new one.";

/** C0's `ConsumerContext`, structurally: only what this page reads. */
type Context = { state: string; account?: { suspended: boolean } };

export type KeyRow = {
  id: string;
  name: string;
  /** The stored display prefix, never more of the key. */
  prefix: string;
  created: string;
  lastUsed: string;
  revoked: string | null;
  revocable: boolean;
};

export type KeysModel = {
  create: { allowed: true } | { allowed: false; reason: string };
  list:
    | { kind: "ready"; rows: KeyRow[] }
    | { kind: "empty" }
    | { kind: "unavailable"; message: string; retry: boolean };
};

const LIST_FAILED = "Your keys could not be loaded right now, so none are shown. Reload the page to try again.";

/** Why creation is refused, and what the list says, for a context that is not a ready account. */
const NOT_READY: Record<string, { reason: string; list: string; retry: boolean }> = {
  unverified: {
    reason: "Verify your email address before creating a key.",
    list: "Verify your email address to manage API keys.",
    retry: false,
  },
  onboarding: {
    reason: "Finish setting up your account before creating a key.",
    list: "Your account is still being set up. Keys appear here once it is ready.",
    retry: false,
  },
  signed_out: { reason: "Sign in to create a key.", list: "Sign in to see your keys.", retry: false },
};
const UNAVAILABLE = {
  reason: "Your account could not be checked right now, so no key can be created. Reload the page to try again.",
  list: LIST_FAILED,
  retry: true,
};

/** UTC, to the minute, cut from the string: a list of instants must not move with the locale. */
function utc(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

function rowOf(key: ApiKeySummary): KeyRow {
  return {
    id: key.id,
    name: key.name,
    prefix: `${key.prefix}…`,
    created: utc(key.created_at),
    lastUsed: key.last_used_at === null ? "Never" : utc(key.last_used_at),
    revoked: key.revoked_at === null ? null : utc(key.revoked_at),
    // R33: revocable whatever the account's state, so a leaked key can always be revoked.
    revocable: key.revoked_at === null,
  };
}

export function keysPageModel(context: Context, keys: Result<ApiKeySummary[]> | null): KeysModel {
  if (context.state !== "ready" || context.account === undefined) {
    const why = Object.hasOwn(NOT_READY, context.state) ? NOT_READY[context.state] : UNAVAILABLE;
    return { create: { allowed: false, reason: why.reason }, list: { kind: "unavailable", message: why.list, retry: why.retry } };
  }
  const create: KeysModel["create"] = context.account.suspended
    ? { allowed: false, reason: "This account is suspended: you cannot create keys, but you can still revoke them." }
    : { allowed: true };
  if (keys === null || !keys.ok) return { create, list: { kind: "unavailable", message: LIST_FAILED, retry: true } };
  if (keys.value.length === 0) return { create, list: { kind: "empty" } };
  return { create, list: { kind: "ready", rows: keys.value.map(rowOf) } };
}

/** What the create dialog shows. The plaintext only for the first, non-replayed response (R16). */
export function createOutcome(result: Result<ApiKeyCreated>): { kind: "secret"; secret: string } | { kind: "notice"; message: string } {
  if (!result.ok) return { kind: "notice", message: result.error.message };
  if (result.value.replayed || result.value.secret === null) return { kind: "notice", message: REPLAYED_COPY };
  return { kind: "secret", secret: result.value.secret };
}

/** C3A's lost-response text (`CREATE_UNKNOWN`, server-only), verbatim: a new key may still have been made. */
export const CREATE_LOST =
  "the key could not be created right now; try again. If a new key appears in your list, revoke it - its secret cannot be shown again";

export const REVOKE_LOST = "The revocation could not be confirmed. Refresh the page to see whether the key is revoked.";

/**
 * A server-action call that rejects (network drop, deploy skew, a 5xx from the action endpoint)
 * becomes a fixed refusal, so the control clears its pending state and says something true. The
 * rejection's own text is never shown.
 */
export async function settle<T>(call: () => Promise<Result<T>>, lost: string): Promise<Result<T>> {
  try {
    return await call();
  } catch {
    return { ok: false, error: { code: "dependency_unavailable", message: lost } };
  }
}

export function revokeConfirmText(name: string): string {
  return `Revoke "${name}"? New requests with this key are refused immediately. This cannot be undone.`;
}
