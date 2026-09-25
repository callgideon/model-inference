/**
 * C3A: the trusted consumer actions, behind narrow ports.
 *
 * Every action takes the server-resolved consumer context (C0's `consumerSession()`), never an
 * identity from the caller: the tenant is `account.orgId`, the creator is `account.userId`, the
 * grant's individual is the session user, the key's audience is the column default (`consumer`, a
 * browser role cannot write it). Inputs are allowlisted, so a smuggled org, role, audience, amount or
 * actor is `invalid_request`, not quietly dropped.
 *
 * Ports:
 * - keys: `public.api_keys` through the individual's OWN JWT (0001's owner insert/update policies and
 *   0004's column grants decide; no service key);
 * - grant: `public.claim_signup_grant` (0015), service role, server-side, with the session user's id
 *   and nothing else from the request. The DB derives verification and owns idempotency (R71); this
 *   module duplicates none of the grant SQL;
 * - operator: `OperatorPort`, which no App-reachable function implements yet (the audited G6B
 *   operations live in the `infrx` schema, which PostgREST does not expose). Without one the action is
 *   an explicit unavailable state.
 *
 * Every failure is a `Result` with fixed text: the DB's message can name relations, constraints or
 * identifiers and never reaches a caller.
 */

if (typeof window !== "undefined") throw new Error("lib/services/actions.ts is server-only");

import {
  API_KEY_CREATE_FIELDS,
  MAX_KEY_NAME_CHARS,
  type ApiKeyCreated,
  type ApiKeySummary,
  type ErrorCode,
  type Result,
} from "../contracts/types.ts";
import { parseCredit, type Credit } from "../contracts/v2/money-units.ts";
import { generateKey, hashKey, keyPrefix } from "../keys.ts";
import { __testables, keyOf, type ConsumerAccount, type ConsumerContext, type RpcClient } from "./console.ts";
import type { Row } from "./query.ts";

const { badInput } = __testables;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Audit metadata on the entitlement (0006); changing it never makes anyone eligible again (R71). */
export const SIGNUP_CAMPAIGN = "app_onboarding";

const MAX_IDEMPOTENCY_KEY_CHARS = 200;
const MAX_REASON_CHARS = 500;
const REPLAY_WINDOW_MS = 10 * 60 * 1000;
const REPLAY_SLOTS = 1000;

function ok<T>(value: T): Result<T> {
  return { ok: true, value };
}

function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

type DbError = { code?: string | null; message?: string | null };
type DbAnswer = { data: unknown; error: DbError | null };

function rowsOf(data: unknown): Row[] {
  if (!Array.isArray(data)) throw new TypeError("the store did not return rows");
  return data as Row[];
}

function refusal<T>(error: DbError, unavailable: string): Result<T> {
  if (error.code === "42501") return fail("forbidden", "this account cannot make that change");
  return fail("dependency_unavailable", unavailable);
}

// ---------------------------------------------------------------------------------------- ports

/** The three `api_keys` calls the actions make, each already scoped by its caller. */
export type KeyStore = {
  insert(row: { org_id: string; created_by: string; name: string; prefix: string; key_hash: string }): PromiseLike<DbAnswer>;
  /** Sets `revoked_at` on the org's still-active consumer key; answers the rows it changed. */
  revoke(orgId: string, keyId: string, at: string): PromiseLike<DbAnswer>;
  find(orgId: string, keyId: string): PromiseLike<DbAnswer>;
};

const KEY_COLUMNS = "id,name,prefix,created_at,last_used_at,revoked_at,trace_mode";

type Filter = PromiseLike<DbAnswer> & {
  eq(column: string, value: string): Filter;
  is(column: string, value: null): Filter;
  select(columns: string): Filter;
};

/** The part of a supabase-js client `supabaseKeyStore` uses; the cookie client fits. */
export type KeyClient = {
  from(relation: "api_keys"): {
    insert(row: Record<string, string>): Filter;
    update(row: Record<string, string>): Filter;
    select(columns: string): Filter;
  };
};

/** `public.api_keys` as the signed-in individual. Only consumer-audience keys are revocable here. */
export function supabaseKeyStore(client: KeyClient): KeyStore {
  const keys = () => client.from("api_keys");
  return {
    insert: (row) => keys().insert(row).select(KEY_COLUMNS),
    revoke: (orgId, keyId, at) =>
      keys()
        .update({ revoked_at: at })
        .eq("id", keyId)
        .eq("org_id", orgId)
        .eq("audience", "consumer")
        .is("revoked_at", null)
        .select(KEY_COLUMNS),
    find: (orgId, keyId) => keys().select(KEY_COLUMNS).eq("id", keyId).eq("org_id", orgId).eq("audience", "consumer"),
  };
}

// -------------------------------------------------------------------------------------- context

/** The account an action may act for, or why not. Suspension refuses new work only (R33). */
function actingAccount<T>(context: ConsumerContext, creating: boolean): ConsumerAccount | Result<T> {
  switch (context.state) {
    case "ready":
      if (creating && context.account.suspended) return fail("org_suspended", "this account is suspended; keys can be revoked but not created");
      return context.account;
    case "unverified":
      return fail("forbidden", "verify your email address first");
    case "onboarding":
      return fail("forbidden", "finish setting up your account first");
    case "signed_out":
      return fail("forbidden", "sign in first");
    default:
      return fail("dependency_unavailable", "your account could not be checked right now; try again");
  }
}

function isAccount<T>(value: ConsumerAccount | Result<T>): value is ConsumerAccount {
  return "walletId" in value;
}

// -------------------------------------------------------------------------------------- actions

/** What the onboarding page learns. `held` names no reason: the reasons are operator data (0015). */
export type GrantOutcome =
  | { status: "granted" | "already_granted"; wallet_id: string; amount: Credit; granted_at: string }
  | { status: "held" };

const CREATE_UNKNOWN =
  "the key could not be created right now; try again. If a new key appears in your list, revoke it - its secret cannot be shown again";

export function createConsumerActions(options: { now?: () => Date } = {}) {
  const now = options.now ?? (() => new Date());
  // ponytail: per-process replay memory (ids and names only, never a secret). It absorbs a
  // double-click and an in-process retry; a retry that lands on another instance after a lost
  // response mints a second key, which the copy tells the individual to revoke. Durable replay needs
  // an idempotency column or a D10 function (wiring request WR-C3A-3).
  const replays = new Map<string, { name: string; at: number; outcome: Promise<Result<ApiKeySummary>> }>();

  async function mint(store: KeyStore, account: ConsumerAccount, name: string, secret: string): Promise<Result<ApiKeySummary>> {
    const { data, error } = await store.insert({
      org_id: account.orgId,
      created_by: account.userId,
      name,
      prefix: keyPrefix(secret),
      key_hash: await hashKey(secret),
    });
    if (error !== null) return refusal(error, CREATE_UNKNOWN);
    const rows = rowsOf(data);
    if (rows.length !== 1) return fail("internal_error", CREATE_UNKNOWN);
    return ok(keyOf(rows[0]));
  }

  async function current(store: KeyStore, account: ConsumerAccount, key: ApiKeySummary): Promise<ApiKeySummary> {
    const { data, error } = await store.find(account.orgId, key.id);
    if (error !== null) return key;
    const rows = rowsOf(data);
    return rows.length === 1 ? keyOf(rows[0]) : key;
  }

  return {
    /** Mint a consumer key. The plaintext is in this response only; a replay carries `secret: null`. */
    async createKey(context: ConsumerContext, store: KeyStore, input: unknown): Promise<Result<ApiKeyCreated>> {
      try {
        const rejected = badInput<ApiKeyCreated>(input, API_KEY_CREATE_FIELDS);
        if (rejected !== null) return rejected;
        const { name, trace_mode, idempotency_key } = input as Record<string, unknown>;
        if (typeof name !== "string" || name.trim() === "" || name.trim().length > MAX_KEY_NAME_CHARS) {
          return fail("invalid_request", `name is 1 to ${MAX_KEY_NAME_CHARS} characters`);
        }
        // Consumer capture is off (P-09); a key cannot opt into it, and the column is not writable.
        if (trace_mode !== undefined && trace_mode !== "off") {
          return fail("unsupported_parameter", "content capture is not available for consumer keys");
        }
        if (
          idempotency_key !== undefined &&
          (typeof idempotency_key !== "string" || idempotency_key === "" || idempotency_key.length > MAX_IDEMPOTENCY_KEY_CHARS)
        ) {
          return fail("invalid_request", "idempotency_key is a non-empty string");
        }
        const account = actingAccount<ApiKeyCreated>(context, true);
        if (!isAccount(account)) return account;
        const label = name.trim();

        const at = now().getTime();
        const slot = idempotency_key === undefined ? null : `${account.userId}\u0000${idempotency_key}`;
        const seen = slot === null ? undefined : replays.get(slot);
        if (seen !== undefined && at - seen.at < REPLAY_WINDOW_MS) {
          if (seen.name !== label) return fail("idempotency_conflict", "this idempotency key was used for a different key");
          const first = await seen.outcome;
          if (!first.ok) return first;
          return ok({ ...(await current(store, account, first.value)), secret: null, replayed: true });
        }

        const secret = generateKey();
        const outcome = mint(store, account, label, secret);
        if (slot !== null) {
          replays.delete(slot);
          replays.set(slot, { name: label, at, outcome });
          if (replays.size > REPLAY_SLOTS) replays.delete(replays.keys().next().value as string);
        }
        const result = await outcome;
        if (!result.ok) {
          if (slot !== null && replays.get(slot)?.outcome === outcome) replays.delete(slot);
          return result;
        }
        return ok({ ...result.value, secret, replayed: false });
      } catch {
        return fail("internal_error", CREATE_UNKNOWN);
      }
    },

    /** Revoke one of the individual's own consumer keys. Idempotent; allowed while suspended (R33). */
    async revokeKey(context: ConsumerContext, store: KeyStore, keyId: unknown): Promise<Result<ApiKeySummary>> {
      try {
        const account = actingAccount<ApiKeySummary>(context, false);
        if (!isAccount(account)) return account;
        if (typeof keyId !== "string" || !UUID.test(keyId)) return fail("not_found", "no such key for this account");
        const unavailable = "the key could not be revoked right now; try again";
        const changed = await store.revoke(account.orgId, keyId, now().toISOString());
        if (changed.error !== null) return refusal(changed.error, unavailable);
        const rows = rowsOf(changed.data);
        if (rows.length === 1) return ok(keyOf(rows[0]));
        // Nothing changed: already revoked (answer that revocation) or not this account's key.
        const found = await store.find(account.orgId, keyId);
        if (found.error !== null) return refusal(found.error, unavailable);
        const existing = rowsOf(found.data);
        if (existing.length === 1 && keyOf(existing[0]).revoked_at !== null) return ok(keyOf(existing[0]));
        return fail("not_found", "no such key for this account");
      } catch {
        return fail("internal_error", "the key could not be revoked; refresh to see its state");
      }
    },

    /**
     * The one-time individual grant, for the session's own user (A2's onboarding and callback call
     * this). Verification, eligibility and idempotency are the DB's (0015); this adds nothing to them.
     */
    async claimGrant(context: ConsumerContext, grants: () => RpcClient): Promise<Result<GrantOutcome>> {
      let userId: string;
      if (context.state === "ready") userId = context.account.userId;
      else if (context.state === "onboarding") userId = context.userId;
      else if (context.state === "unverified") return fail("forbidden", "verify your email address first");
      else if (context.state === "signed_out") return fail("forbidden", "sign in first");
      else return fail("dependency_unavailable", "your account could not be checked right now; try again");

      const unavailable = "your signup credit could not be checked right now; try again";
      let client: RpcClient;
      try {
        client = grants();
      } catch {
        return fail("dependency_unavailable", unavailable);
      }
      try {
        const { data, error } = await client.rpc("claim_signup_grant", {
          p_user_id: userId,
          p_campaign_version: SIGNUP_CAMPAIGN,
          p_operation_id: null,
        });
        if (error !== null) return fail("dependency_unavailable", unavailable);
        const rows = rowsOf(data);
        if (rows.length !== 1 || rows[0].user_id !== userId) return fail("internal_error", unavailable);
        const row = rows[0];
        switch (row.status) {
          case "granted":
          case "replayed":
            if (typeof row.wallet_id !== "string" || typeof row.granted_at !== "string") return fail("internal_error", unavailable);
            return ok({
              status: row.status === "granted" ? "granted" : "already_granted",
              wallet_id: row.wallet_id,
              amount: parseCredit(row.amount),
              granted_at: row.granted_at,
            });
          case "unverified":
            return fail("forbidden", "verify your email address first");
          case "identity_reused":
          case "rollout_hold":
          case "retired":
            return ok({ status: "held" });
          default:
            return fail("internal_error", unavailable);
        }
      } catch {
        return fail("internal_error", unavailable);
      }
    },
  };
}

export type ConsumerActions = ReturnType<typeof createConsumerActions>;

// ------------------------------------------------------------------------------ cross-site guard

/**
 * A cookie-authenticated mutation must come from this origin. Next already refuses a server action
 * whose Origin differs from the host; this is the same rule stated where the actions are, so it
 * holds whatever the framework configuration says and is testable here.
 */
export function sameOrigin(headers: Headers): boolean {
  const origin = headers.get("origin");
  const host = (headers.get("x-forwarded-host") ?? headers.get("host"))?.split(",")[0].trim();
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

// ------------------------------------------------------------------------------------- operator

type Audited = { reason: string; idempotency_key: string; actor: string };

/** The privileged operations (the G6B set U3 needs). There is no "set balance". */
export type OperatorCommand =
  | ({ action: "adjust_credit"; user_id: string; amount: Credit } & Audited)
  | ({ action: "grant_initial"; user_id: string } & Audited)
  | ({ action: "set_suspension"; org_id: string; suspended: boolean } & Audited)
  | ({ action: "revoke_key"; key_id: string } & Audited);

export type OperatorPort = { run(command: OperatorCommand): Promise<Result<{ replayed: boolean }>> };

const OPERATOR_FIELDS: Record<OperatorCommand["action"], readonly string[]> = {
  adjust_credit: ["user_id", "amount"],
  grant_initial: ["user_id"],
  set_suspension: ["org_id", "suspended"],
  revoke_key: ["key_id"],
};

/**
 * Validate an operator request into a command. Operator authority comes from the session; the audit
 * actor is `operator:<session user>`, never a form value; a reason and an idempotency key are required.
 */
export function operatorCommand(session: { userId: string; isOperator: boolean }, input: unknown): Result<OperatorCommand> {
  if (!session.isOperator) return fail("forbidden", "operator authority is required");
  const action = (input as { action?: unknown } | null)?.action;
  if (typeof action !== "string" || !Object.hasOwn(OPERATOR_FIELDS, action)) {
    return fail("invalid_request", "unknown operator action");
  }
  const own = OPERATOR_FIELDS[action as OperatorCommand["action"]];
  const rejected = badInput<OperatorCommand>(input, ["action", "reason", "idempotency_key", ...own]);
  if (rejected !== null) return rejected;
  const fields = input as Record<string, unknown>;
  const reason = typeof fields.reason === "string" ? fields.reason.trim() : "";
  if (reason === "" || reason.length > MAX_REASON_CHARS) return fail("invalid_request", `a reason of 1 to ${MAX_REASON_CHARS} characters is required`);
  const key = fields.idempotency_key;
  if (typeof key !== "string" || key === "" || key.length > MAX_IDEMPOTENCY_KEY_CHARS) {
    return fail("invalid_request", "an idempotency key is required");
  }
  for (const id of ["user_id", "org_id", "key_id"]) {
    if (own.includes(id) && (typeof fields[id] !== "string" || !UUID.test(fields[id] as string))) {
      return fail("invalid_request", `${id} must be an id`);
    }
  }
  const audited: Audited = { reason, idempotency_key: key, actor: `operator:${session.userId}` };
  switch (action) {
    case "adjust_credit": {
      let amount: Credit;
      try {
        if (typeof fields.amount !== "string") throw new TypeError();
        amount = parseCredit(fields.amount);
      } catch {
        return fail("invalid_request", "amount is an exact CREDIT decimal string");
      }
      if (/^-?0\.0+$/.test(amount)) return fail("invalid_request", "an adjustment is not zero");
      return ok({ action, user_id: fields.user_id as string, amount, ...audited });
    }
    case "grant_initial":
      return ok({ action, user_id: fields.user_id as string, ...audited });
    case "set_suspension":
      if (typeof fields.suspended !== "boolean") return fail("invalid_request", "suspended is true or false");
      return ok({ action, org_id: fields.org_id as string, suspended: fields.suspended, ...audited });
    default:
      return ok({ action: "revoke_key", key_id: fields.key_id as string, ...audited });
  }
}

/** Run a validated command. No port is an explicit unavailable state, never a silent success. */
export async function runOperatorCommand(command: OperatorCommand, port: OperatorPort | null): Promise<Result<{ replayed: boolean }>> {
  if (port === null) {
    return fail("dependency_unavailable", "operator changes are not available in the console yet; use the audited operations CLI");
  }
  try {
    return await port.run(command);
  } catch {
    return fail("dependency_unavailable", "the operator change could not be confirmed; retry with the same idempotency key");
  }
}
