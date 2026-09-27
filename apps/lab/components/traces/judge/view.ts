// V3: judge rows and calibration copy, derived only from the J2/J3 records. An estimate never looks
// like a result, a media-dependent criterion is never scored from text, and "calibrated" is said only
// when the statistics behind it are present.
import type { Calibration, JudgeRefusal, JudgeRun } from "./port.ts";

export const JUDGE_COPY: Record<JudgeRefusal | "empty" | "dry_run" | "no_verdict_dry_run" | "no_media" | "no_media_verdict" | "uncalibrated", string> = {
  denied: "Your role in this workspace sees aggregate health only, not judge results.",
  unavailable: "Judge results could not be read. Nothing is shown until they can be; try again shortly.",
  empty: "No judge runs for this request.",
  dry_run: "Dry run: an estimate only. Nothing was sent to a judge and nothing was scored.",
  no_verdict_dry_run: "no score (dry run)",
  no_media: "not scored (no media)",
  no_media_verdict: "no pass: judged without media",
  uncalibrated: "Not calibrated: no operator labels yet. These are the judge's scores alone.",
};

export type JudgeRow = { id: string; label: string; status: string; scores: [string, string][]; verdict: string; calibration: string };

const pct = (x: number) => `${Math.round(x * 100)}%`;

export function calibrationCopy(c: Calibration): string {
  if (c.state === "calibrated" && c.labels >= c.required && c.agreement !== null && c.interval !== null)
    return `Calibrated against ${c.labels} operator labels: agreement ${pct(c.agreement)} (95% CI ${pct(c.interval[0])}–${pct(c.interval[1])}).`;
  if (c.labels === 0) return JUDGE_COPY.uncalibrated;
  if (c.labels < c.required) return `Not calibrated: ${c.labels} of ${c.required} operator labels needed.`;
  return `Not calibrated yet: ${c.labels} operator labels, agreement not computed.`;
}

function status(r: JudgeRun): string {
  if (r.mode === "dry_run") return `Estimated worst case ${r.reservedUsd} USD; nothing reserved, nothing sent.`;
  if (r.state === "ambiguous") return `Submit outcome unknown: ${r.reservedUsd} USD held until reconciled; it is never resubmitted.`;
  if (r.state === "released") return "Released; nothing charged.";
  if (r.state === "collected") return `Collected; ${r.actualUsd} USD charged.`;
  return `Awaiting the judge: ${r.reservedUsd} USD held.`;
}

export function judgeRows(runs: JudgeRun[]): JudgeRow[] {
  return runs.map((r) => {
    const dry = r.mode === "dry_run";
    const scored = !dry && r.state === "collected";
    const blind = !r.media && r.scores.some((s) => s.requiresMedia);
    return {
      id: r.runId,
      label: dry ? JUDGE_COPY.dry_run : `Live judge result · ${r.judgeModel} · rubric v${r.rubricVersion}`,
      status: status(r),
      scores: scored
        ? r.scores.map((s): [string, string] => [s.criterion, s.requiresMedia && !r.media ? JUDGE_COPY.no_media : s.score === null ? "not scored" : `${s.score}/${s.max}`])
        : [],
      verdict: dry ? JUDGE_COPY.no_verdict_dry_run : !scored || r.overallPass === null ? "—" : blind ? JUDGE_COPY.no_media_verdict : r.overallPass ? "pass" : "fail",
      calibration: calibrationCopy(r.calibration),
    };
  });
}
