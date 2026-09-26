/**
 * U3: the operator forms' rules, kept out of the `.tsx` so node --test can check them (R48).
 *
 * - A form carries ONE idempotency key until a change commits. Any failure - refused, conflicting
 *   or not confirmed - keeps it, so resubmitting (a retry, a double click, a lost response) replays
 *   the same change instead of applying a second one. Only a committed answer rotates it.
 * - A form submits exactly its operation's fields (C3A's `operatorCommand` allowlist); the actor is
 *   never a field, the server takes it from the session and the database from the JWT.
 */

import type { Result } from "../../../lib/contracts/types.ts";

export type OperatorFormAction = "adjust_credit" | "set_suspension" | "revoke_key";

export type OperatorField = {
  name: string;
  label: string;
  kind: "id" | "amount" | "suspended" | "reason";
  hint?: string;
};

const REASON: OperatorField = { name: "reason", label: "Reason (recorded in the audit trail)", kind: "reason" };

export const OPERATOR_FORMS: readonly { action: OperatorFormAction; title: string; description: string; submit: string; fields: OperatorField[] }[] = [
  {
    action: "adjust_credit",
    title: "Adjust credit",
    description:
      "A signed CREDIT adjustment to one individual's wallet: positive adds credit, negative corrects. It is a new ledger entry, never an edit, and it cannot take the wallet below what is reserved.",
    submit: "Apply adjustment",
    fields: [
      { name: "user_id", label: "Individual (user id)", kind: "id" },
      { name: "amount", label: "Amount (CREDIT)", kind: "amount", hint: "Exact decimal, up to 8 places, e.g. 250 or -12.5" },
      REASON,
    ],
  },
  {
    action: "set_suspension",
    title: "Suspend or restore",
    description:
      "A suspended organization cannot create keys or start new work. Its reads and key revocation keep working, and nothing already recorded changes.",
    submit: "Apply",
    fields: [{ name: "org_id", label: "Organization id", kind: "id" }, { name: "suspended", label: "Status", kind: "suspended" }, REASON],
  },
  {
    action: "revoke_key",
    title: "Revoke a consumer key",
    description: "Revokes one consumer API key immediately, for example a leaked one. A revoked key never works again.",
    submit: "Revoke key",
    fields: [{ name: "key_id", label: "Key id", kind: "id" }, REASON],
  },
];

/** The operation's input, from a form's fields: exactly its own names, plus the key. */
export function formInput(action: OperatorFormAction, get: (name: string) => string | null, idempotencyKey: string): Record<string, unknown> {
  const form = OPERATOR_FORMS.find((f) => f.action === action);
  if (form === undefined) throw new TypeError(`no operator form ${action}`);
  const input: Record<string, unknown> = { action };
  for (const field of form.fields) {
    const value = get(field.name) ?? "";
    input[field.name] = field.kind === "suspended" ? value === "true" : value;
  }
  input.idempotency_key = idempotencyKey;
  return input;
}

/** The key the next submission carries: the same one until a change committed. */
export function nextKey(current: string, result: Result<{ replayed: boolean }>, fresh: () => string): string {
  return result.ok ? fresh() : current;
}

export type Outcome = { tone: "ok" | "error"; text: string };

/** What the operator is told. A committed change says so; nothing else claims success. */
export function outcomeOf(result: Result<{ replayed: boolean }>): Outcome {
  if (result.ok) {
    return result.value.replayed
      ? { tone: "ok", text: "Already applied: this request was recorded earlier, and nothing changed twice." }
      : { tone: "ok", text: "Committed. The change is recorded once in the audit trail." };
  }
  switch (result.error.code) {
    case "idempotency_conflict":
      return { tone: "error", text: "This form's idempotency key already recorded a different change. Reload the page to start a new one." };
    case "dependency_unavailable":
      return { tone: "error", text: `Not confirmed: ${result.error.message}. Submitting again sends the same idempotency key, so it cannot apply twice.` };
    default:
      return { tone: "error", text: `Refused: ${result.error.message}.` };
  }
}
