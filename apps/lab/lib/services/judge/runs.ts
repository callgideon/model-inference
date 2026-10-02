// J3 / WR-V3-1, AP-09 09c: the judge runs of one request, behind V3's JudgePort. The Lab no longer
// calls the lab_judge_runs RPC, and AP-08's API has no per-request read yet (WIRING REQUEST
// WR-AP09L-3: `GET /lab/v1/traces/{request_id}/judge-runs`), so the read is honestly unavailable:
// never a direct-database fallback, never an empty list that reads as "not judged". A viewer is
// denied as before (judge results are developer+).
import type { JudgePort } from "../../../components/traces/judge/port.ts";

export function judgeRunsPort(): JudgePort {
  return {
    async runs(actor) {
      return { ok: false, reason: actor.role === "viewer" ? "denied" : "unavailable" };
    },
  };
}
