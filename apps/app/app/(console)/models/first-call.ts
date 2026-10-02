/**
 * UX-04 (C-02): the /models first-call guide - Create key -> Make a test call -> View your request -
 * decided from the account's own scoped reads: U2's `keysPageModel` (C0 `keys()`) and the first page
 * of C0 `requests()`. Completion is a persisted succeeded request in that page, never a click or a
 * browser flag. The page is bounded (newest first), so one without a success proves nothing while a
 * cursor says more rows exist, and a failed read proves nothing at all: the guide is then labelled
 * "Quickstart" and claims neither completion nor "no request yet" (05-service-contracts CX-07).
 *
 * Pure and client-safe. Imported by `node --test`: relative `.ts` imports only (R48).
 */
import type { KeysModel } from "../api-keys/view-model.ts";
import type { Page, Result } from "../../../lib/contracts/types.ts";
import type { ConsumerRequest } from "../../../lib/contracts/v2/consumer.ts";

export const FIRST_REQUEST = "Make your first request";
export const QUICKSTART = "Quickstart";

export type FirstCall = {
  heading: typeof FIRST_REQUEST | typeof QUICKSTART;
  /** False once a success is on record: the guide collapses to a compact Quickstart. */
  open: boolean;
  key:
    | { kind: "create" }
    | { kind: "existing"; keys: { name: string; prefix: string }[] }
    | { kind: "refused"; reason: string }
    | { kind: "unavailable" };
  canCreate: boolean;
  done: { requestId: string } | null;
};

export function firstCallModel(keys: KeysModel, requests: Result<Page<ConsumerRequest>> | null): FirstCall {
  const canCreate = keys.create.allowed;
  let key: FirstCall["key"];
  if (!keys.create.allowed) key = { kind: "refused", reason: keys.create.reason };
  else if (keys.list.kind === "unavailable") key = { kind: "unavailable" };
  else {
    const active = keys.list.kind === "ready" ? keys.list.rows.filter((row) => row.revoked === null) : [];
    key = active.length === 0 ? { kind: "create" } : { kind: "existing", keys: active.map(({ name, prefix }) => ({ name, prefix })) };
  }

  const read = requests !== null && requests.ok ? requests.value : null;
  const success = read?.items.find((row) => row.state === "succeeded");
  const done = success === undefined ? null : { requestId: success.request_id };
  // "No request yet" only from a read that answered with everything it has.
  const none = read !== null && done === null && read.next_cursor === null;
  return { heading: none ? FIRST_REQUEST : QUICKSTART, open: done === null, key, canCreate, done };
}
