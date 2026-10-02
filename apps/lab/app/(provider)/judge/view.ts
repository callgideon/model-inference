// UX-08 (L-08 judge setup): the judge records' view model over AP-08's API documents
// (apps/infrx-api/openapi/lab-control.json: RunDoc, ConfigDoc, CalibrationDoc, BudgetDoc, Money), as
// lib/services/judge/records.ts reads them (WR-UX08-3). UX08-J01 pins the fields to the committed OpenAPI.
import type { Outcome } from "@/lib/services/judge/core";
import type { Budget, Calibration, Config, JudgeRun, Money } from "@/lib/services/judge/records";

export type { Budget, Calibration, Config, JudgeRecords, JudgeRun, Money } from "@/lib/services/judge/records";

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
} as const satisfies Record<JudgeRun["domain_state"], string>;
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
