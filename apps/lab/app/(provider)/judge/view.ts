// UX-08 (L-08 judge setup): the judge records' view model over AP-08's API documents
// (apps/infrx-api/openapi/lab-control.json: RunDoc, ConfigDoc, CalibrationDoc, BudgetDoc, Money).
// ponytail: the field subsets are copied here because packages/api-client is not yet regenerated with
// the judge paths (WR-UX08-3); then they become `components["schemas"][…]`. UX08-J01 pins them to the
// committed OpenAPI meanwhile.
import type { Outcome } from "@/lib/services/judge/core";

export type Money = { amount: string; unit: "CREDIT" | "USD" | "PROVIDER_USD" };
export type Calibration = { state: "calibrated" | "insufficient" | "uncalibrated"; labels: number; required: number; agreement: number | null; interval: number[] | null };
export type Config = { config_id: string; model_id: string; judge_model: string; rubric_version: number; sample_size: number; grantor_org_id: string; created_at: string; calibration: Calibration };
export type JudgeRun = {
  run_id: string; config_id: string; domain_state: keyof typeof RUN_STATE; cancel_requested: boolean; payer_ref: string;
  sample_size: number; selected: number; sent: number; accepted: number; rejected: number; requested_at: string;
  reserved: Money | null; settled: Money | null;
};
export type Budget = { payer_ref: string; limit: Money; reserved: Money; settled: Money };
export type JudgeRecords = { configs: Config[]; runs: JudgeRun[]; budgets: Budget[] };

const money = (m: Money | null) => (m === null ? "none" : `${m.amount} ${m.unit}`);

export const RUN_STATE = {
  queued: "queued",
  reserved: "budget reserved, not sent",
  submitting: "sending",
  submitted: "sent, awaiting the judge",
  ambiguous: "outcome unknown: not resent automatically",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
} as const;
const ENDED = new Set(["completed", "failed", "cancelled"]);

/** "calibrated" only when the API says so; otherwise how many reference labels of how many. */
export function calibration(c: Calibration): string {
  if (c.state === "calibrated") return `calibrated: agreement ${c.agreement} [${c.interval?.join(", ")}] over ${c.labels} reference labels`;
  return `not calibrated: ${c.labels} of ${c.required} reference labels`;
}

export function configRow(c: Config) {
  return { id: c.config_id, model: c.model_id, judge: c.judge_model, rubric: `rubric v${c.rubric_version}`, sample: `${c.sample_size} samples`, calibration: calibration(c.calibration) };
}

export function runRow(r: JudgeRun) {
  const cancelling = r.cancel_requested && !ENDED.has(r.domain_state);
  return {
    id: r.run_id, config: r.config_id, payer: r.payer_ref, requested: r.requested_at,
    state: `${RUN_STATE[r.domain_state]}${cancelling ? " · cancel requested" : ""}`,
    counts: `${r.sent} of ${r.selected} selected sent · ${r.accepted} accepted · ${r.rejected} rejected`,
    money: [`reserved ${money(r.reserved)}`, `settled ${money(r.settled)}`],
  };
}

export function budgetRow(b: Budget) {
  return { payer: b.payer_ref, limit: money(b.limit), reserved: money(b.reserved), settled: money(b.settled) };
}

/** Register row 98: whether a keyed form resends its Idempotency-Key on the next submission. Only after
 *  an unknown outcome (unavailable: the write may have happened), so the retry replays it; any definite
 *  answer ends that submission and the next one is a new write with a new key. */
export const retryKeepsKey = (o: Outcome) => !o.ok && o.reason === "unavailable";
