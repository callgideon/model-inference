// V3: the judge runs of one request, as J2 records them (codex/w5-judge 16660999: dry run or live, the
// external_run state, the PROVIDER_USD hold) with J3's calibration of that judge configuration.
// AP-09 09c: the Lab's judge-runs port (lib/services/judge/runs.ts); unavailable until the API serves a
// request's runs (WR-AP09L-3). Tests drive fake.ts.
import type { Actor, Result } from "../detail/port.ts";
import { judgeRunsPort } from "../../../lib/services/judge/runs.ts";

export type Score = { criterion: string; score: number | null; max: number; requiresMedia: boolean };
/** J3: agreement with operator labels only (C3F provenance), with its interval; `required` is J3's minimum. */
export type Calibration = {
  state: "uncalibrated" | "insufficient" | "calibrated";
  labels: number;
  required: number;
  agreement: number | null;
  interval: [number, number] | null;
};
export type JudgeRun = {
  runId: string;
  mode: "dry_run" | "live";
  state: "estimated" | "reserved" | "submitted" | "ambiguous" | "collected" | "released";
  judgeModel: string;
  rubricVersion: number;
  media: boolean;
  reservedUsd: string; // PROVIDER_USD, an exact decimal string; never CREDIT
  actualUsd: string | null;
  scores: Score[];
  overallPass: boolean | null;
  calibration: Calibration;
};
export type JudgeRefusal = "denied" | "unavailable";

export interface JudgePort {
  runs(actor: Actor, requestId: string): Promise<Result<JudgeRun[], JudgeRefusal>>;
}

/** The wiring seam for WR-V3-1: the Lab's judge-runs port, never a fake. */
export function judgePort(): JudgePort {
  return judgeRunsPort();
}
