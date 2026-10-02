/**
 * U3: C3A's `OperatorPort` over infrx-api's operator actions (AP-09 09b): `POST
 * /operator/v1/credit-adjustments | suspensions | key-revocations`, each with the form's reason and
 * its idempotency key as the `Idempotency-Key` header.
 *
 * The API derives operator authority and the audit actor from the signed-in session and runs the
 * same audited, idempotent operation the CLI uses; the `actor` a command carries is never sent, so
 * nobody can name someone else as the actor. Every failure is a typed code with fixed text; an
 * answer this cannot read is "not confirmed", never a success, and the form keeps its idempotency
 * key so a retry replays instead of applying twice.
 *
 * Server only (imported by `app/actions.ts`); pure `.ts` with relative imports so node --test loads it.
 */

import type { ConsumerApi } from "../../../lib/api/index.ts";
import type { ErrorCode, Result } from "../../../lib/contracts/types.ts";
import type { OperatorCommand, OperatorPort } from "../../../lib/services/actions.ts";

const UNCONFIRMED = "the operator change could not be confirmed; retry with the same idempotency key";

/** Refusals an operator may act on; anything else is "not confirmed". */
const REFUSALS: Partial<Record<string, [ErrorCode, string]>> = {
  forbidden: ["forbidden", "operator authority is required"],
  idempotency_conflict: ["idempotency_conflict", "this idempotency key already recorded a different change"],
  invalid_request: ["invalid_request", "the API refused this change as invalid"],
  not_found: ["not_found", "no such individual, organization or key"],
  state_conflict: ["state_conflict", "the target is not in a state this change applies to"],
};

function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

type Answer = Awaited<ReturnType<ConsumerApi["call"]>>;

function send(api: ConsumerApi, command: OperatorCommand): Promise<Answer> | null {
  const audited = { idempotencyKey: command.idempotency_key };
  switch (command.action) {
    case "adjust_credit":
      return api.call("post", "/operator/v1/credit-adjustments", { ...audited, body: { user_id: command.user_id, amount: command.amount, reason: command.reason } });
    case "set_suspension":
      return api.call("post", "/operator/v1/suspensions", { ...audited, body: { org_id: command.org_id, suspended: command.suspended, reason: command.reason } });
    case "revoke_key":
      return api.call("post", "/operator/v1/key-revocations", { ...audited, body: { key_id: command.key_id, reason: command.reason } });
    default:
      return null;
  }
}

export function apiOperatorPort(api: () => Promise<ConsumerApi>): OperatorPort {
  return {
    async run(command) {
      try {
        const call = send(await api(), command);
        // The one-time signup grant is claimed by the individual (A2); a held grant is the CLI's.
        if (call === null) return fail("unsupported_parameter", "the signup grant is not an operator console change");
        const answer = await call;
        if (!answer.ok) {
          const { error } = answer;
          if (error.status === 401 || error.status === 403) return fail(...(REFUSALS.forbidden as [ErrorCode, string]));
          const known = error.kind === "error" ? REFUSALS[error.code] : undefined;
          return known === undefined ? fail("dependency_unavailable", UNCONFIRMED) : fail(...known);
        }
        const replayed = (answer.data as { replayed?: unknown } | null)?.replayed;
        if (typeof replayed !== "boolean") return fail("dependency_unavailable", UNCONFIRMED);
        return { ok: true, value: { replayed } };
      } catch {
        return fail("dependency_unavailable", UNCONFIRMED);
      }
    },
  };
}
