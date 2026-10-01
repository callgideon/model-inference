// C3L: the Lab's judge configuration, budget, run-request and calibration actions, pure so they run
// under node --test (actions.ts binds them to the guard and the session). Authority stays in the
// lab-sql RPCs (SR-C3L-1: each checks auth.uid()'s current membership and the grantor's current
// external_judging grant on infrx.now()); this layer never sends an identity, takes the provider only
// from the guarded workspace, refuses a lower role and malformed ids before any call, bounds every
// page, and fails closed.
import type { Membership, Role } from "../../auth/access.ts";
import { authCookieOptions, labConfig } from "../../auth/config.ts";
import { USD_RE as USD, UUID, UUID_RE as ID } from "../shapes.ts";

export type Rpc = (name: string, args: Record<string, unknown>) => PromiseLike<{ data: unknown; error: unknown }>;
export type Outcome = { ok: true; data: unknown } | { ok: false; reason: "invalid" | "denied" | "unavailable" };
type Input = Record<string, unknown>;

export const RPC = {
  configure: "lab_judge_configure",
  budget: "lab_judge_set_budget",
  request: "lab_judge_request_run",
  calibration: "lab_judge_calibration",
} as const;
export const PAGE_MAX = 50;
export const MAX_SAMPLES = 200; // J1's candidate scan bound
// packages/shared REF_RE for kind `payer` (node --test cannot strip types under node_modules).
const PAYER = new RegExp(`^lab:payer:(${UUID}):${UUID}@sha256:[0-9a-f]{64}$`);
const MODEL = /^[a-z0-9][a-z0-9.-]{0,63}$/;
const RANK: Record<Role, number> = { viewer: 0, developer: 1, administrator: 2 };
// What the RPCs raise for a caller they refuse (insufficient_privilege, no_data_found).
const DENIED = new Set(["42501", "P0002"]);

type Cookie = { name: string; value: string; options?: object };
type CookieStore = { getAll(): Cookie[]; set(name: string, value: string, options?: object): unknown };
type ClientOptions = {
  cookieOptions: ReturnType<typeof authCookieOptions>;
  cookies: { getAll(): Cookie[]; setAll(list: Cookie[]): void };
};

/** The user's own Lab session client (its cookie, its env), as guard.ts builds it; a misconfigured
 * Lab builds none and every call is unavailable. */
export function sessionRpc(env: Record<string, string | undefined>, store: CookieStore,
  make: (url: string, key: string, options: ClientOptions) => { rpc: Rpc }): Rpc {
  const config = labConfig(env);
  if (config === null) return async () => ({ data: null, error: { code: "misconfigured" } });
  const client = make(config.supabaseUrl, config.anonKey, {
    cookieOptions: authCookieOptions(config),
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list) => list.forEach(({ name, value, options }) => store.set(name, value, options)),
    },
  });
  return (name, args) => client.rpc(name, args);
}

const INVALID: Outcome = { ok: false, reason: "invalid" };
const REFUSED: Outcome = { ok: false, reason: "denied" };
const UNAVAILABLE: Outcome = { ok: false, reason: "unavailable" };

const id = (v: unknown) => (typeof v === "string" && ID.test(v) ? v : null);
const count = (v: unknown, max: number) => {
  const n = typeof v === "string" && /^[0-9]{1,6}$/.test(v) ? Number(v) : NaN;
  return n >= 1 && n <= max ? n : null;
};
/** This provider's own named payer; another provider's payer is not a payer here. */
const payerOf = (v: unknown, w: Membership) => (typeof v === "string" && PAYER.exec(v)?.[1] === w.providerId ? v : null);

async function call(rpc: Rpc, name: string, args: Record<string, unknown>): Promise<Outcome> {
  try {
    const { data, error } = await rpc(name, args);
    if (!error) return { ok: true, data };
    return DENIED.has((error as { code?: unknown }).code as string) ? REFUSED : UNAVAILABLE;
  } catch {
    return UNAVAILABLE;
  }
}

export async function configure(rpc: Rpc, w: Membership, input: Input): Promise<Outcome> {
  if (RANK[w.role] < RANK.developer) return REFUSED;
  const grantor = id(input.grantor_org_id);
  const model = id(input.model_id);
  const judge = typeof input.judge_model === "string" && MODEL.test(input.judge_model) ? input.judge_model : null;
  const rubric = count(input.rubric_version, 65_535);
  const samples = count(input.sample_size, MAX_SAMPLES);
  if (!grantor || !model || !judge || rubric === null || samples === null) return INVALID;
  return call(rpc, RPC.configure, {
    p_provider_org_id: w.providerId, p_grantor_org_id: grantor, p_model_id: model,
    p_judge_model: judge, p_rubric_version: rubric, p_sample_size: samples,
  });
}

export async function setBudget(rpc: Rpc, w: Membership, input: Input): Promise<Outcome> {
  if (RANK[w.role] < RANK.administrator) return REFUSED;
  const payer = payerOf(input.payer_ref, w);
  const limit = typeof input.limit_usd === "string" && USD.test(input.limit_usd) ? input.limit_usd : null;
  if (!payer || !limit) return INVALID;
  return call(rpc, RPC.budget, { p_provider_org_id: w.providerId, p_payer_ref: payer, p_limit: { unit: "PROVIDER_USD", value: limit } });
}

/** `run_id` is minted when the form renders, so a double click repeats it and the RPC answers
 * the same run; the action never mints one. */
export async function requestRun(rpc: Rpc, w: Membership, input: Input): Promise<Outcome> {
  if (RANK[w.role] < RANK.developer) return REFUSED;
  const run = id(input.run_id);
  const config = id(input.config_id);
  const payer = payerOf(input.payer_ref, w);
  if (!run || !config || !payer) return INVALID;
  return call(rpc, RPC.request, { p_provider_org_id: w.providerId, p_run_id: run, p_config_id: config, p_payer_ref: payer });
}

/** One bounded page (keyset on `label_id`); a server page longer than asked is not rendered. */
export async function listCalibration(rpc: Rpc, w: Membership, input: Input): Promise<Outcome> {
  if (RANK[w.role] < RANK.developer) return REFUSED;
  const cursor = input.after ?? "";
  const after = cursor === "" ? null : id(cursor);
  const limit = input.limit === undefined || input.limit === "" ? PAGE_MAX : count(input.limit, 999_999);
  if ((cursor !== "" && !after) || limit === null) return INVALID;
  const size = Math.min(limit, PAGE_MAX);
  const out = await call(rpc, RPC.calibration, { p_provider_org_id: w.providerId, p_after: after, p_limit: size });
  if (!out.ok) return out;
  if (!Array.isArray(out.data) || out.data.length > size) return UNAVAILABLE;
  const rows = out.data as { label_id?: unknown }[];
  return { ok: true, data: { rows, next: rows.length === size ? (rows[rows.length - 1].label_id ?? null) : null } };
}
