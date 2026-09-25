/**
 * U3: C3A's `OperatorPort` over the operator console's audited RPCs (WR-U3-1, D10).
 *
 * The client is the signed-in operator's OWN Supabase client (cookie JWT, no service key). Each
 * command is one `public.operator_*` function, which checks `public.is_operator()` in the database,
 * derives the audit actor from the JWT subject, and runs the same audited, idempotent `infrx`
 * operation the CLI uses (`grant_credit`, `set_suspension`, `revoke_key`). So a consumer or provider
 * session that crafts the call is refused by the database, not only by this route; and the
 * `actor` a command carries is never sent - nobody can name someone else as the actor.
 *
 * Every failure is a typed code with fixed text (a DB message can name relations and ids); an
 * answer this cannot read is "not confirmed", never a success, and the form keeps its idempotency
 * key so a retry replays instead of applying twice.
 *
 * Server only (imported by `app/actions.ts`); pure `.ts` with relative imports so node --test loads it.
 */

import type { ErrorCode, Result } from "../../../lib/contracts/types.ts";
import type { OperatorCommand, OperatorPort } from "../../../lib/services/actions.ts";

type Answer = { data: unknown; error: { code?: string | null; message?: string | null } | null };

/** The part of a supabase-js client this port calls; the cookie client fits. */
export type OperatorRpcClient = { rpc(fn: string, args: Record<string, unknown>): PromiseLike<Answer> };

export const OPERATOR_RPC = {
  adjust_credit: "operator_adjust_credit",
  set_suspension: "operator_set_suspension",
  revoke_key: "operator_revoke_key",
} as const;

const UNCONFIRMED = "the operator change could not be confirmed; retry with the same idempotency key";

/** `infrx.refuse` codes an operator may act on; anything else is "not confirmed". */
const REFUSALS: Partial<Record<string, [ErrorCode, string]>> = {
  forbidden: ["forbidden", "operator authority is required"],
  idempotency_conflict: ["idempotency_conflict", "this idempotency key already recorded a different change"],
  invalid_request: ["invalid_request", "the database refused this change as invalid"],
  not_found: ["not_found", "no such individual, organization or key"],
  state_conflict: ["state_conflict", "the target is not in a state this change applies to"],
};

function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

function refused(error: NonNullable<Answer["error"]>): Result<{ replayed: boolean }> {
  if (error.code === "42501") return fail(...(REFUSALS.forbidden as [ErrorCode, string]));
  if (error.code === "P0002") return fail(...(REFUSALS.not_found as [ErrorCode, string]));
  if (error.code === "P0001") {
    const known = REFUSALS[/^([a-z_]+):/.exec(error.message ?? "")?.[1] ?? ""];
    if (known !== undefined) return fail(...known);
  }
  return fail("dependency_unavailable", UNCONFIRMED);
}

function call(command: OperatorCommand): [string, Record<string, unknown>] | null {
  const audited = { p_reason: command.reason, p_idempotency_key: command.idempotency_key };
  switch (command.action) {
    case "adjust_credit":
      return [OPERATOR_RPC.adjust_credit, { p_user: command.user_id, p_amount: command.amount, ...audited }];
    case "set_suspension":
      return [OPERATOR_RPC.set_suspension, { p_org: command.org_id, p_suspended: command.suspended, ...audited }];
    case "revoke_key":
      return [OPERATOR_RPC.revoke_key, { p_key: command.key_id, ...audited }];
    default:
      return null;
  }
}

export function operatorRpcPort(client: () => Promise<OperatorRpcClient>): OperatorPort {
  return {
    async run(command) {
      const target = call(command);
      // The one-time signup grant is claimed by the individual (A2); a held grant is the CLI's.
      if (target === null) return fail("unsupported_parameter", "the signup grant is not an operator console change");
      try {
        const { data, error } = await (await client()).rpc(...target);
        if (error !== null) return refused(error);
        const replayed = (data as { replayed?: unknown } | null)?.replayed;
        if (Array.isArray(data) || typeof replayed !== "boolean") return fail("dependency_unavailable", UNCONFIRMED);
        return { ok: true, value: { replayed } };
      } catch {
        return fail("dependency_unavailable", UNCONFIRMED);
      }
    },
  };
}
