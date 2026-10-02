// J3 / WR-V3-1, AP-09 09c (register row 98): the judge runs of one request, behind V3's JudgePort,
// over api-judge-2's `GET /lab/v1/traces/{request_id}/judge-runs` (WR-AP09L-3; 0043's door run as the
// session user, so membership and the current external_judging grant are the server's). This adapter
// sends only the guarded workspace and the request, and fails closed: a viewer is denied before any
// call (judge results are developer+), a refusal keeps its meaning, and an answer it cannot read whole
// is unavailable - never a direct-database fallback, never a partial list that reads as complete.
import type { JudgePort, JudgeRun, Score } from "../../../components/traces/judge/port.ts";
import type { LabApi } from "../../api/index.ts";
import { sessionApi } from "../../auth/session.ts";
import { USD_RE } from "../shapes.ts";
import { reasonOf } from "./core.ts";

const STATES = ["reserved", "submitted", "ambiguous", "collected", "released"];
const CALIBRATION = ["uncalibrated", "insufficient", "calibrated"];

type Row = Record<string, unknown>;
const int = (v: unknown) => (Number.isInteger(v) ? (v as number) : bad());
const str = (v: unknown) => (typeof v === "string" ? v : bad());
const bool = (v: unknown) => (typeof v === "boolean" ? v : bad());
const opt = <T>(v: unknown, read: (v: unknown) => T) => (v === null || v === undefined ? null : read(v));
const one = <T>(v: unknown, allowed: string[]) => (allowed.includes(v as string) ? (v as T) : bad());
function bad(): never {
  throw new Error("unreadable");
}
/** A PROVIDER_USD amount as its exact string; CREDIT or USD is never a judge cost. */
const usd = (v: unknown) => ((v as Row).unit === "PROVIDER_USD" && USD_RE.test((v as Row).amount as string) ? ((v as Row).amount as string) : bad());

/** One TraceJudgeRun as V3's JudgeRun. Every listed run SENT this request, so it is live: a dry run
 *  sends nothing and the door never lists it. */
function run(r: Row): JudgeRun {
  const c = r.calibration as Row;
  const interval = opt(c.interval, (v) => (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "number") ? (v as [number, number]) : bad()));
  return {
    runId: str(r.run_id),
    mode: "live",
    state: one(r.state, STATES),
    judgeModel: str(r.judge_model),
    rubricVersion: int(r.rubric_version),
    media: bool(r.media),
    reservedUsd: usd(r.reserved),
    actualUsd: opt(r.actual, usd),
    scores: (r.scores as Row[]).map((s): Score => ({ criterion: str(s.criterion), score: int(s.score), max: int(s.max_score), requiresMedia: bool(s.requires_media) })),
    overallPass: opt(r.overall_pass, bool),
    calibration: {
      state: one(c.state, CALIBRATION),
      labels: int(c.labels),
      required: int(c.required),
      agreement: opt(c.agreement, (v) => (typeof v === "number" ? v : bad())),
      interval,
    },
  };
}

export function judgeRunsPort(api: LabApi | null = sessionApi()): JudgePort {
  return {
    async runs(actor, requestId) {
      if (actor.role === "viewer") return { ok: false, reason: "denied" };
      if (api === null) return { ok: false, reason: "unavailable" };
      const answer = await api.call("get", "/lab/v1/traces/{request_id}/judge-runs", { params: { request_id: requestId }, query: { provider_org_id: actor.providerId } });
      if (!answer.ok) return { ok: false, reason: reasonOf(answer.error) === "denied" ? "denied" : "unavailable" };
      try {
        const page = answer.data as { data: Row[]; next_cursor?: unknown };
        // ponytail: 0043's door lists every run of the request in one page; follow next_cursor if it ever pages.
        if (page.next_cursor) return { ok: false, reason: "unavailable" };
        return { ok: true, value: page.data.map(run) }; // a non-list or non-object row throws: unavailable
      } catch {
        return { ok: false, reason: "unavailable" };
      }
    },
  };
}
