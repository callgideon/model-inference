/**
 * C-07 / AP-09: the consumer's data-use controls over infrx-api (AP-07a's grantor routes,
 * `infrx/gateway/routes/console_data_use.py`). Server-only. The API owns every rule that is not a
 * form's: the actor is the verified session's own organization and its owner, the consent version is
 * the compare-and-set token (409 `state_conflict` when it moved), a suspended account changes no
 * capture but may withdraw a grant, and replays are state-based (the same choice again writes
 * nothing). Here: the read as an honest state, the input allowlist (nothing names an organization,
 * evaluation consent is never given from here), one Idempotency-Key per submission, the Origin
 * check before anything runs, a refresh only after an acknowledged change, and fixed text for
 * every failure (the API's message never reaches the page).
 *
 * Not here: creating a provider grant (POST /console/v1/data-grants) - the App has no provider
 * directory to choose a recipient from; grants made elsewhere are listed and can be withdrawn.
 */

if (typeof window !== "undefined") throw new Error("lib/services/data-use is server-only");

import type { components } from "@infrx/api-client/consumer";
import type { ApiError } from "@infrx/api-client/transport";
import type { ConsumerApi } from "../../api/index.ts";
import { Malformed, optionalInstant } from "../../api/result.ts";
import type { ErrorCode, Result } from "../../contracts/types.ts";
import { sameOrigin } from "../actions.ts";

type S = components["schemas"];
export type Mode = "off" | "minimal" | "full";
const MODES: readonly string[] = ["off", "minimal", "full"];
const GRANT_STATES: readonly string[] = ["active", "expired", "revoked"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_IDEMPOTENCY_KEY_CHARS = 200;
/** What a capture change carries when no consent was decided yet: the API's own default. */
const DEFAULT_RETENTION_DAYS = 30;

export type KeyCapture = { id: string; name: string; mode: Mode; effectiveMode: Mode };
export type DataGrant = {
  id: string;
  providerOrgId: string;
  modelIds: string[];
  categories: string[];
  purposes: string[];
  retentionDays: number;
  expiresAt: string | null;
  revokedAt: string | null;
  state: "active" | "expired" | "revoked";
};
export type DataUse = { consentVersion: number; retentionDays: number; keys: KeyCapture[]; grants: DataGrant[] };
export type DataUseRead = { kind: "ready"; value: DataUse } | { kind: "unavailable" } | { kind: "forbidden" } | { kind: "signed_out" };

export type CaptureInput = { key_id: string; mode: Mode; consent_version: number; retention_days: number; idempotency_key: string };
export type WithdrawInput = { grant_id: string; idempotency_key: string };

function mode(value: unknown): Mode {
  if (typeof value !== "string" || !MODES.includes(value)) throw new Malformed("mode");
  return value as Mode;
}

function grantOf(g: S["Grant"]): DataGrant {
  if (!GRANT_STATES.includes(g.state)) throw new Malformed("grant state");
  return {
    id: g.grant_id,
    providerOrgId: g.provider_org_id,
    modelIds: [...g.model_ids],
    categories: [...g.categories],
    purposes: [...g.purposes],
    retentionDays: g.retention_days,
    expiresAt: optionalInstant(g.expires_at),
    revokedAt: optionalInstant(g.revoked_at),
    state: g.state,
  };
}

function dataUseOf(doc: S["DataUseDoc"]): DataUse {
  if (!Number.isInteger(doc.consent.version)) throw new Malformed("consent version");
  return {
    consentVersion: doc.consent.version,
    retentionDays: doc.consent.retention_days ?? DEFAULT_RETENTION_DAYS,
    keys: doc.keys.map((k) => ({ id: k.key_id, name: k.name, mode: mode(k.mode), effectiveMode: mode(k.effective_mode) })),
    grants: doc.grants.map(grantOf),
  };
}

/**
 * The signed-in owner's data-use record. A 401 is a session that ended: the routes answer an API key
 * with 401 too, but the App only ever forwards the session, so that case is not reachable from here.
 */
export async function readDataUse(api: ConsumerApi): Promise<DataUseRead> {
  try {
    const answer = await api.call("get", "/console/v1/data-use");
    if (!answer.ok) {
      if (answer.error.status === 401) return { kind: "signed_out" };
      return answer.error.kind === "error" && answer.error.code === "forbidden" ? { kind: "forbidden" } : { kind: "unavailable" };
    }
    return { kind: "ready", value: dataUseOf(answer.data) };
  } catch {
    return { kind: "unavailable" };
  }
}

// ----------------------------------------------------------------------------------- the changes

const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const fail = <T>(code: ErrorCode, message: string): Result<T> => ({ ok: false, error: { code, message } });

const UNCONFIRMED = "the change was not confirmed; try again - the same choice is applied once";
const REFUSALS: Record<string, string> = {
  state_conflict: "your data-use settings changed since this page loaded; reload and choose again",
  org_suspended: "this account is suspended; capture cannot be changed",
  forbidden: "only the account's owner can change its data use",
  invalid_request: "that choice is not allowed",
};

function refused<T>(error: ApiError, notFound: string): Result<T> {
  if (error.status === 401) return fail("forbidden", "your session has ended; sign in again");
  const code = error.kind === "error" ? error.code : null;
  if (code === "not_found") return fail("not_found", notFound);
  const message = code === null ? undefined : REFUSALS[code];
  return message === undefined ? fail("dependency_unavailable", UNCONFIRMED) : fail(code as ErrorCode, message);
}

/** The fields of `input` if it is an object holding only `allowed` ones, else null. */
function fields(input: unknown, allowed: readonly string[]): Record<string, unknown> | null {
  if (typeof input !== "object" || input === null || Array.isArray(input)) return null;
  const entries = Object.entries(input).filter(([, value]) => value !== undefined);
  return entries.every(([name]) => allowed.includes(name)) ? Object.fromEntries(entries) : null;
}

const idempotencyKey = (value: unknown) => typeof value === "string" && value !== "" && value.length <= MAX_IDEMPOTENCY_KEY_CHARS;
const BAD = "that choice is not allowed";

/** Set one key's capture mode under the consent version the page read. */
export async function setCapture(api: ConsumerApi, input: unknown): Promise<Result<DataUse>> {
  const f = fields(input, ["key_id", "mode", "consent_version", "retention_days", "idempotency_key"]);
  if (f === null || typeof f.key_id !== "string" || !UUID.test(f.key_id) || typeof f.mode !== "string" || !MODES.includes(f.mode)) {
    return fail("invalid_request", BAD);
  }
  const { consent_version: version, retention_days: days } = f;
  if (!Number.isInteger(version) || (version as number) < 0 || !Number.isInteger(days) || (days as number) < 1 || (days as number) > 90) {
    return fail("invalid_request", BAD);
  }
  if (!idempotencyKey(f.idempotency_key)) return fail("invalid_request", "an idempotency key is required");
  try {
    const answer = await api.call("put", "/console/v1/keys/{key_id}/capture", {
      params: { key_id: f.key_id },
      body: { mode: f.mode as Mode, consent_version: version as number, retention_days: days as number, evaluation_consent: false },
      idempotencyKey: f.idempotency_key as string,
    });
    return answer.ok ? ok(dataUseOf(answer.data)) : refused(answer.error, "no such key for this account");
  } catch {
    return fail("dependency_unavailable", UNCONFIRMED);
  }
}

/** Withdraw one of the account's own grants (allowed while suspended; a second withdrawal replays). */
export async function withdrawGrant(api: ConsumerApi, input: unknown): Promise<Result<DataGrant>> {
  const f = fields(input, ["grant_id", "idempotency_key"]);
  if (f === null || typeof f.grant_id !== "string" || !UUID.test(f.grant_id)) return fail("not_found", "no such grant for this account");
  if (!idempotencyKey(f.idempotency_key)) return fail("invalid_request", "an idempotency key is required");
  try {
    const answer = await api.call("delete", "/console/v1/data-grants/{grant_id}", {
      params: { grant_id: f.grant_id },
      idempotencyKey: f.idempotency_key as string,
    });
    return answer.ok ? ok(grantOf(answer.data)) : refused(answer.error, "no such grant for this account");
  } catch {
    return fail("dependency_unavailable", UNCONFIRMED);
  }
}

/** What `app/(console)/settings/actions.ts` supplies: Next's request APIs and the request's client. */
export type DataUseDeps = { headers(): Promise<Headers>; api(): Promise<ConsumerApi>; revalidate(path: string): void };

/** The settings actions, composed: nothing runs for a cross-site request, nothing refreshes a failure. */
export function dataUseActions(deps: DataUseDeps) {
  async function guarded<T>(run: (api: ConsumerApi) => Promise<Result<T>>): Promise<Result<T>> {
    if (!sameOrigin(await deps.headers())) return fail("forbidden", "this change must be made from the console itself");
    const result = await run(await deps.api());
    if (result.ok) deps.revalidate("/settings");
    return result;
  }
  return {
    setCapture: (input: CaptureInput) => guarded((api) => setCapture(api, input)),
    withdrawGrant: (input: WithdrawInput) => guarded((api) => withdrawGrant(api, input)),
  };
}
