// UX-08 WR-UX08-3: the judge page's records - the workspace's judge configurations, runs and budgets
// over AP-08's list reads (`GET /lab/v1/judge/{configs,runs,budgets}`, each door run as the session
// user, developer+) through the generated Lab client. The read sends only the guarded workspace and
// fails closed: any refusal or an answer it cannot read whole is null (the section's unavailable
// state), never one list without the others or a partial list read as complete. The judge's only
// unit is PROVIDER_USD (0031's lab_budgets check), so any other unit or an inexact amount is unreadable.
import type { LabApi } from "../../api/index.ts";
import type { Actor } from "../../auth/access.ts";
import { sessionApi } from "../../auth/session.ts";
import { USD_RE } from "../shapes.ts";

export const RUN_STATES = ["queued", "reserved", "submitting", "submitted", "ambiguous", "completed", "failed", "cancelled"] as const;
const CALIBRATION = ["calibrated", "insufficient", "uncalibrated"] as const;

export type Money = { amount: string; unit: "PROVIDER_USD" };
export type Calibration = { state: (typeof CALIBRATION)[number]; labels: number; required: number; agreement: number | null; interval: number[] | null };
export type Config = { config_id: string; model_id: string; judge_model: string; rubric_version: number; sample_size: number; grantor_org_id: string; created_at: string; calibration: Calibration };
export type JudgeRun = {
  run_id: string; config_id: string; domain_state: (typeof RUN_STATES)[number]; cancel_requested: boolean; payer_ref: string;
  sample_size: number; selected: number; sent: number; accepted: number; rejected: number; requested_at: string;
  reserved: Money | null; settled: Money | null;
};
export type Budget = { payer_ref: string; limit: Money; reserved: Money; settled: Money };
/** `more`: a configurations or runs list has a further page the section does not show. */
export type JudgeRecords = { configs: Config[]; runs: JudgeRun[]; budgets: Budget[]; more: boolean };

type Row = Record<string, unknown>;
function bad(): never {
  throw new Error("unreadable");
}
const str = (v: unknown) => (typeof v === "string" ? v : bad());
const int = (v: unknown) => (Number.isInteger(v) ? (v as number) : bad());
const bool = (v: unknown) => (typeof v === "boolean" ? v : bad());
const opt = <T>(v: unknown, read: (v: unknown) => T) => (v === null || v === undefined ? null : read(v));
const one = <T extends string>(v: unknown, allowed: readonly T[]) => (allowed.includes(v as T) ? (v as T) : bad());
const usd = (v: unknown): Money => ((v as Row).unit === "PROVIDER_USD" && USD_RE.test((v as Row).amount as string) ? { amount: (v as Row).amount as string, unit: "PROVIDER_USD" } : bad());
const rows = (page: unknown) => (page as { data: Row[] }).data; // a non-list throws at .map: unreadable

function config(r: Row): Config {
  const c = r.calibration as Row;
  return {
    config_id: str(r.config_id), model_id: str(r.model_id), judge_model: str(r.judge_model), rubric_version: int(r.rubric_version),
    sample_size: int(r.sample_size), grantor_org_id: str(r.grantor_org_id), created_at: str(r.created_at),
    calibration: {
      state: one(c.state, CALIBRATION), labels: int(c.labels), required: int(c.required),
      agreement: opt(c.agreement, (v) => (typeof v === "number" ? v : bad())),
      interval: opt(c.interval, (v) => (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "number") ? (v as number[]) : bad())),
    },
  };
}

function run(r: Row): JudgeRun {
  return {
    run_id: str(r.run_id), config_id: str(r.config_id), domain_state: one(r.domain_state, RUN_STATES), cancel_requested: bool(r.cancel_requested),
    payer_ref: str(r.payer_ref), sample_size: int(r.sample_size), selected: int(r.selected), sent: int(r.sent), accepted: int(r.accepted),
    rejected: int(r.rejected), requested_at: str(r.requested_at), reserved: opt(r.reserved, usd), settled: opt(r.settled, usd),
  };
}

const budget = (r: Row): Budget => ({ payer_ref: str(r.payer_ref), limit: usd(r.limit), reserved: usd(r.reserved), settled: usd(r.settled) });

export async function readJudgeRecords(actor: Pick<Actor, "providerId">, api: LabApi | null = sessionApi()): Promise<JudgeRecords | null> {
  if (api === null) return null;
  const query = { provider_org_id: actor.providerId };
  // ponytail: one page of each (the route's maximum, keyset on id, not recency); `more` says when there are more.
  const [configs, runs, budgets] = await Promise.all([
    api.call("get", "/lab/v1/judge/configs", { query: { ...query, limit: 100 } }),
    api.call("get", "/lab/v1/judge/runs", { query: { ...query, limit: 100 } }),
    api.call("get", "/lab/v1/judge/budgets", { query }),
  ]);
  if (!configs.ok || !runs.ok || !budgets.ok) return null;
  try {
    return {
      configs: rows(configs.data).map(config),
      runs: rows(runs.data).map(run),
      budgets: rows(budgets.data).map(budget),
      more: Boolean(configs.data.next_cursor || runs.data.next_cursor),
    };
  } catch {
    return null; // a non-object row or a field it cannot read
  }
}
