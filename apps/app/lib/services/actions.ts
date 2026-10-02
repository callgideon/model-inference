/**
 * C3A over infrx-api (AP-09 09b): the trusted consumer actions, each one API call as the signed-in
 * user. The API owns every rule that is not a form's: key minting and hashing, the durable
 * idempotency of a creation (a retried create under the same key replays the first key, with no
 * secret), suspension, ownership, the operator's authority and the audit actor. Here: the input
 * allowlist (a smuggled org, role, audience, amount or actor is `invalid_request`, not quietly
 * dropped), the Origin check before anything runs, a refresh only after an acknowledged change,
 * and fixed text for every failure (the API's message never reaches a caller).
 *
 * `consoleActions` is the composition `app/actions.ts` exports; `app/actions.ts` only hands it
 * Next's request APIs and the client, so every rule is testable here (R48).
 */

if (typeof window !== "undefined") throw new Error("lib/services/actions.ts is server-only");

import type { ApiError } from "@infrx/api-client/transport";
import type { ConsumerApi } from "../api/index.ts";
import { instant, optionalInstant } from "../api/result.ts";
import {
  API_KEY_CREATE_FIELDS,
  FEEDBACK_INPUT_FIELDS,
  MAX_KEY_NAME_CHARS,
  type ApiKeyCreated,
  type ApiKeyCreateInput,
  type ApiKeySummary,
  type ErrorCode,
  type FeedbackInput,
  type Result,
} from "../contracts/types.ts";
import { parseCredit, type Credit } from "../contracts/v2/money-units.ts";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const MAX_IDEMPOTENCY_KEY_CHARS = 200;
const MAX_REASON_CHARS = 500;

function ok<T>(value: T): Result<T> {
  return { ok: true, value };
}

function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

/** An object whose every set field is allowlisted, or the refusal. */
function badInput<T>(input: unknown, allowed: readonly string[]): Result<T> | null {
  if (typeof input !== "object" || input === null || Array.isArray(input)) return fail("invalid_request", "this operation takes an object");
  for (const [name, value] of Object.entries(input)) {
    if (value !== undefined && !allowed.includes(name)) return fail("invalid_request", `${name} is not a field of this request`);
  }
  return null;
}

/** The API's refusal as one of `known`'s fixed texts; a dead session is "sign in"; else `unknown`. */
function refused<T>(error: ApiError, known: Partial<Record<string, string>>, unknown: string): Result<T> {
  if (error.status === 401) return fail("forbidden", "your session has ended; sign in again");
  const code = error.kind === "error" ? error.code : null;
  const message = code === null ? undefined : known[code];
  return message === undefined ? fail("dependency_unavailable", unknown) : fail(code as ErrorCode, message);
}

// -------------------------------------------------------------------------------------- keys

export const CREATE_UNKNOWN =
  "the key could not be created right now; try again. If a new key appears in your list, revoke it - its secret cannot be shown again";
const KEY_REFUSALS: Partial<Record<string, string>> = {
  org_suspended: "this account is suspended; keys can be revoked but not created",
  forbidden: "this account cannot create keys yet; finish setting it up first",
  idempotency_conflict: "this idempotency key was used for a different key",
  invalid_request: "that key name is not allowed",
  not_found: "no such key for this account",
};

type ApiKey = { key_id: string; name: string; prefix: string; created_at: string; revoked_at?: string | null };

function summaryOf(key: ApiKey): ApiKeySummary {
  return {
    id: key.key_id,
    name: key.name,
    prefix: key.prefix,
    created_at: instant(key.created_at),
    last_used_at: null,
    revoked_at: optionalInstant(key.revoked_at),
    trace_mode: null,
  };
}

/**
 * Mint a consumer key through the API. The plaintext is in the first answer only; a replay under
 * the same idempotency key - a double submit, or a retry after a lost answer, on any instance - is
 * the same key with `secret: null` (the dialog says: revoke it and create another).
 */
export async function createKey(api: ConsumerApi, input: unknown): Promise<Result<ApiKeyCreated>> {
  const rejected = badInput<ApiKeyCreated>(input, API_KEY_CREATE_FIELDS);
  if (rejected !== null) return rejected;
  const { name, trace_mode, idempotency_key } = input as Record<string, unknown>;
  if (typeof name !== "string" || name.trim() === "" || name.trim().length > MAX_KEY_NAME_CHARS) {
    return fail("invalid_request", `name is 1 to ${MAX_KEY_NAME_CHARS} characters`);
  }
  // Consumer capture is off (P-09); a key cannot opt into it here.
  if (trace_mode !== undefined && trace_mode !== "off") {
    return fail("unsupported_parameter", "content capture is not available for consumer keys");
  }
  if (idempotency_key !== undefined && (typeof idempotency_key !== "string" || idempotency_key === "" || idempotency_key.length > MAX_IDEMPOTENCY_KEY_CHARS)) {
    return fail("invalid_request", "idempotency_key is a non-empty string");
  }
  try {
    const answer = await api.call("post", "/console/v1/keys", {
      body: { name: name.trim() },
      idempotencyKey: (idempotency_key as string | undefined) ?? crypto.randomUUID(),
    });
    if (!answer.ok) return refused(answer.error, KEY_REFUSALS, CREATE_UNKNOWN);
    const { key, secret, secret_returned, replayed } = answer.data;
    const shown = secret_returned && !replayed && typeof secret === "string" ? secret : null;
    return ok({ ...summaryOf(key), secret: shown, replayed: replayed || shown === null });
  } catch {
    return fail("dependency_unavailable", CREATE_UNKNOWN);
  }
}

/** Revoke one of the individual's own consumer keys. Idempotent; allowed while suspended (R33). */
export async function revokeKey(api: ConsumerApi, keyId: unknown): Promise<Result<ApiKeySummary>> {
  if (typeof keyId !== "string" || !UUID.test(keyId)) return fail("not_found", "no such key for this account");
  const unknown = "the key could not be revoked right now; refresh to see its state";
  try {
    const answer = await api.call("delete", "/console/v1/keys/{key_id}", { params: { key_id: keyId } });
    return answer.ok ? ok(summaryOf(answer.data)) : refused(answer.error, KEY_REFUSALS, unknown);
  } catch {
    return fail("dependency_unavailable", unknown);
  }
}

// ---------------------------------------------------------------------------------- feedback

const FEEDBACK_UNKNOWN = "your feedback could not be confirmed; send it again - the same submission is recorded once";
const FEEDBACK_REFUSALS: Partial<Record<string, string>> = {
  not_found: "no such request for this account",
  invalid_request: "that feedback does not fit the request's signal",
  idempotency_conflict: "this submission key was already used for different feedback",
  org_suspended: "this account is suspended; feedback cannot be sent",
  forbidden: "this account cannot send feedback",
};

export type FeedbackAck = { id: string; request_id: string; created_at: string; replayed: boolean };

/**
 * One feedback signal on one of the caller's own requests (C3F). The API derives the org, the
 * author, the channel and the role, and acknowledges only what it stored; the idempotency key is
 * required (a retried submit replays the first acceptance).
 */
export async function submitFeedback(api: ConsumerApi, input: unknown): Promise<Result<FeedbackAck>> {
  const rejected = badInput<FeedbackAck>(input, FEEDBACK_INPUT_FIELDS);
  if (rejected !== null) return rejected;
  const { request_id, name, value, comment, idempotency_key } = input as Record<string, unknown>;
  if (typeof request_id !== "string" || !UUID.test(request_id)) return fail("not_found", FEEDBACK_REFUSALS.not_found!);
  if (typeof idempotency_key !== "string" || idempotency_key === "" || idempotency_key.length > MAX_IDEMPOTENCY_KEY_CHARS) {
    return fail("invalid_request", "an idempotency key is required");
  }
  if (typeof name !== "string" || !["boolean", "number", "string"].includes(typeof value)) return fail("invalid_request", FEEDBACK_REFUSALS.invalid_request!);
  try {
    const answer = await api.call("post", "/console/v1/requests/{request_id}/feedback", {
      params: { request_id },
      body: { name, value: value as boolean | number | string, comment: typeof comment === "string" ? comment : null },
      idempotencyKey: idempotency_key,
    });
    if (!answer.ok) return refused(answer.error, FEEDBACK_REFUSALS, FEEDBACK_UNKNOWN);
    const ack = answer.data;
    if (ack.channel !== "console" || ack.request_id !== request_id) return fail("internal_error", FEEDBACK_UNKNOWN);
    return ok({ id: ack.feedback_id, request_id: ack.request_id, created_at: instant(ack.created_at), replayed: ack.replayed === true });
  } catch {
    // The transport answers every failure as a Result; only an unreadable acknowledgment lands here.
    return fail("internal_error", FEEDBACK_UNKNOWN);
  }
}

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

// --------------------------------------------------------------------------------- the seam

/** What `app/actions.ts` supplies: Next's request APIs and the request's client, resolved per call. */
export type ActionDeps = {
  headers(): Promise<Headers>;
  api(): Promise<ConsumerApi>;
  session(): Promise<{ userId: string; isOperator: boolean }>;
  endSession(): Promise<void>;
  revalidate(path: string): void;
  /** The audited operator port (`app/(console)/admin/operator-port.ts`). */
  operator?: OperatorPort;
};

/** The console's server actions, composed: nothing runs for a cross-site request, nothing refreshes a failure. */
export function consoleActions(deps: ActionDeps) {
  async function guarded<T>(run: () => Promise<Result<T>>, refresh?: string): Promise<Result<T>> {
    if (!sameOrigin(await deps.headers())) return fail("forbidden", "this change must be made from the console itself");
    const result = await run();
    if (result.ok && refresh !== undefined) deps.revalidate(refresh);
    return result;
  }
  return {
    async signOut(): Promise<void> {
      if (sameOrigin(await deps.headers())) await deps.endSession();
    },
    createKey: (input: ApiKeyCreateInput) => guarded(async () => createKey(await deps.api(), input), "/api-keys"),
    revokeKey: (keyId: string) => guarded(async () => revokeKey(await deps.api(), keyId), "/api-keys"),
    submitFeedback: (input: FeedbackInput) => guarded(async () => submitFeedback(await deps.api(), input)),
    operator: (input: unknown) =>
      guarded(async () => {
        const command = operatorCommand(await deps.session(), input);
        return command.ok ? runOperatorCommand(command.value, deps.operator ?? null) : command;
      }),
  };
}
