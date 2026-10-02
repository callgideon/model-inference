// C3L, AP-09 09c: the Lab's judge configuration, budget, run-request and calibration actions over
// AP-08's `/lab/v1/judge/*` (replaces the lab_judge_* RPCs), pure so they run under node --test
// (actions.ts binds them to the guard and the session). Authority stays in the routes (each runs the
// door as the session user: current membership, the grantor's current external_judging grant, on
// the database clock); this layer never sends an identity, takes the provider only from the guarded
// workspace, refuses a workspace without the capability and malformed ids before any call, and fails
// closed. Every write carries the key its form minted at render (a double submit is one write).
import type { ApiError, LabApi } from "../../api/index.ts";
import { holds, type Actor } from "../../auth/access.ts";
import { USD_RE as USD, UUID, UUID_RE as ID } from "../shapes.ts";

export type Outcome = { ok: true; data: unknown } | { ok: false; reason: "invalid" | "denied" | "conflict" | "unavailable" };
type Input = Record<string, unknown>;

export const MAX_SAMPLES = 200; // the route's bound (ConfigBody.sample_size)
export const MAX_RUBRIC = 1000; // the route's bound (ConfigBody.rubric_version)
// packages/shared REF_RE for kind `payer` (node --test cannot strip types under node_modules).
const PAYER = new RegExp(`^lab:payer:(${UUID}):${UUID}@sha256:[0-9a-f]{64}$`);
const MODEL = /^[a-z0-9][a-z0-9.-]{0,63}$/;

const INVALID: Outcome = { ok: false, reason: "invalid" };
const REFUSED: Outcome = { ok: false, reason: "denied" };
const UNAVAILABLE: Outcome = { ok: false, reason: "unavailable" };

const id = (v: unknown) => (typeof v === "string" && ID.test(v) ? v : null);
const count = (v: unknown, max: number) => {
  const n = typeof v === "string" && /^[0-9]{1,6}$/.test(v) ? Number(v) : NaN;
  return n >= 1 && n <= max ? n : null;
};
/** This provider's own named payer; another provider's payer is not a payer here. */
const payerOf = (v: unknown, w: Actor) => (typeof v === "string" && PAYER.exec(v)?.[1] === w.providerId ? v : null);

/** A route's refusal as the action's reason: the session or the workspace refused is denied, a
 *  reused key with other values a conflict, a shape the route rejects invalid; anything else unavailable. */
export function reasonOf(error: ApiError): Exclude<Outcome, { ok: true }>["reason"] {
  if (error.kind === "unavailable") return "unavailable"; // unreadable, whatever its status
  if ([401, 403, 404].includes(error.status)) return "denied";
  return error.status === 409 ? "conflict" : error.status === 422 ? "invalid" : "unavailable";
}

const answer = (r: Awaited<ReturnType<LabApi["call"]>>): Outcome => (r.ok ? { ok: true, data: r.data } : { ok: false, reason: reasonOf(r.error) });

export async function configure(api: LabApi | null, w: Actor, input: Input): Promise<Outcome> {
  if (!holds(w, "run_evaluation")) return REFUSED;
  const grantor = id(input.grantor_org_id);
  const model = id(input.model_id);
  const judge = typeof input.judge_model === "string" && MODEL.test(input.judge_model) ? input.judge_model : null;
  const rubric = count(input.rubric_version, MAX_RUBRIC);
  const samples = count(input.sample_size, MAX_SAMPLES);
  const key = id(input.idempotency_key);
  if (!grantor || !model || !judge || rubric === null || samples === null || !key) return INVALID;
  if (api === null) return UNAVAILABLE;
  return answer(await api.call("post", "/lab/v1/judge/configs", {
    query: { provider_org_id: w.providerId }, idempotencyKey: key,
    body: { grantor_org_id: grantor, model_id: model, judge_model: judge, rubric_version: rubric, sample_size: samples },
  }));
}

export async function setBudget(api: LabApi | null, w: Actor, input: Input): Promise<Outcome> {
  if (!holds(w, "manage_members")) return REFUSED;
  const payer = payerOf(input.payer_ref, w);
  const limit = typeof input.limit_usd === "string" && USD.test(input.limit_usd) ? input.limit_usd : null;
  const key = id(input.idempotency_key);
  if (!payer || !limit || !key) return INVALID;
  if (api === null) return UNAVAILABLE;
  return answer(await api.call("put", "/lab/v1/judge/budgets/{payer_ref}", {
    params: { payer_ref: payer }, query: { provider_org_id: w.providerId }, idempotencyKey: key,
    body: { limit: { amount: limit, unit: "PROVIDER_USD" } },
  }));
}

/** `run_id` is minted when the form renders and is the run's Idempotency-Key (the route derives the
 *  run id from it), so a double click repeats it and the route answers the same run; the action never
 *  mints one. */
export async function requestRun(api: LabApi | null, w: Actor, input: Input): Promise<Outcome> {
  if (!holds(w, "run_evaluation")) return REFUSED;
  const run = id(input.run_id);
  const config = id(input.config_id);
  const payer = payerOf(input.payer_ref, w);
  if (!run || !config || !payer) return INVALID;
  if (api === null) return UNAVAILABLE;
  return answer(await api.call("post", "/lab/v1/judge/runs", {
    query: { provider_org_id: w.providerId }, idempotencyKey: run, body: { config_id: config, payer_ref: payer },
  }));
}

/** A configuration's calibration as the route reports it (state, labels, agreement): never computed here. */
export async function calibration(api: LabApi | null, w: Actor, input: Input): Promise<Outcome> {
  if (!holds(w, "run_evaluation")) return REFUSED;
  const config = id(input.config_id);
  if (!config) return INVALID;
  if (api === null) return UNAVAILABLE;
  return answer(await api.call("get", "/lab/v1/judge/calibration", { query: { provider_org_id: w.providerId, config_id: config } }));
}
