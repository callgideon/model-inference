// J3 / WR-V3-1: the judge runs of one request (J2's run record + J3's calibration of its
// configuration), read through lab-sql's `lab_judge_runs` on the user's own session. The RPC is the
// authority (developer+ of the provider, that provider's runs only); this adapter sends only the
// session's provider, makes no call for a viewer or a malformed id, and fails the whole read closed on
// any row it cannot fully read - including a no-media pass (R56) or a "calibrated" claim without the
// labels and statistics behind it (J3).
import type { Actor, Result } from "../../../components/traces/detail/port.ts";
import type { JudgePort, JudgeRefusal, JudgeRun } from "../../../components/traces/judge/port.ts";
import type { Rpc } from "./core.ts";

export const RUNS_RPC = "lab_judge_runs";
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const USD = /^(0|[1-9][0-9]{0,11})\.[0-9]{8}$/; // PROVIDER_USD, exact to 1e-8 (R159)
const MODES = ["dry_run", "live"];
const STATES = ["estimated", "reserved", "submitted", "ambiguous", "collected", "released"];
const CALIBRATION = ["uncalibrated", "insufficient", "calibrated"];

const int = (v: unknown, min: number) => Number.isInteger(v) && (v as number) >= min;
const num = (v: unknown) => typeof v === "number" && v >= -1 && v <= 1;

function score(s: unknown): boolean {
  const x = s as Record<string, unknown>;
  return typeof x === "object" && x !== null && typeof x.criterion === "string" && (x.score === null || int(x.score, 0))
    && int(x.max, 1) && typeof x.requiresMedia === "boolean";
}

function readable(row: unknown): row is JudgeRun {
  if (typeof row !== "object" || row === null) return false;
  const r = row as Record<string, unknown>;
  const c = r.calibration as Record<string, unknown>;
  const interval = c?.interval as unknown[] | null;
  const shaped = typeof r.runId === "string" && ID.test(r.runId) && MODES.includes(r.mode as string)
    && STATES.includes(r.state as string) && typeof r.judgeModel === "string" && int(r.rubricVersion, 1)
    && typeof r.media === "boolean" && typeof r.reservedUsd === "string" && USD.test(r.reservedUsd)
    && (r.actualUsd === null || (typeof r.actualUsd === "string" && USD.test(r.actualUsd)))
    && Array.isArray(r.scores) && r.scores.every(score) && (r.overallPass === null || typeof r.overallPass === "boolean")
    && typeof c === "object" && c !== null && CALIBRATION.includes(c.state as string) && int(c.labels, 0)
    && int(c.required, 1) && (c.agreement === null || num(c.agreement))
    && (interval === null || (Array.isArray(interval) && interval.length === 2 && interval.every(num) && (interval[0] as number) <= (interval[1] as number)));
  if (!shaped) return false;
  const run = row as JudgeRun;
  const blindPass = !run.media && run.overallPass === true && run.scores.some((s) => s.requiresMedia);
  const cal = run.calibration;
  const unsupported = cal.state === "calibrated" && (cal.labels < cal.required || cal.agreement === null || cal.interval === null);
  return !blindPass && !unsupported;
}

export function rpcJudgePort(session: () => Promise<Rpc>): JudgePort {
  return {
    async runs(actor: Actor, requestId: string): Promise<Result<JudgeRun[], JudgeRefusal>> {
      if (actor.role === "viewer") return { ok: false, reason: "denied" };
      if (typeof requestId !== "string" || !ID.test(requestId)) return { ok: false, reason: "unavailable" };
      try {
        const rpc = await session();
        const { data, error } = await rpc(RUNS_RPC, { p_provider_org_id: actor.providerId, p_request_id: requestId });
        if (error) {
          const code = (error as { code?: unknown }).code;
          return { ok: false, reason: code === "42501" || code === "P0002" ? "denied" : "unavailable" };
        }
        if (!Array.isArray(data) || !data.every(readable)) return { ok: false, reason: "unavailable" };
        return { ok: true, value: data };
      } catch {
        return { ok: false, reason: "unavailable" };
      }
    },
  };
}
