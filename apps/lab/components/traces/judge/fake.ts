// V3: an in-memory stand-in for the J2/J3 read (WR-V3-1), for tests/v. Developer+ only, as C3L's
// actions are; another provider's runs are never returned.
import type { Actor, Result } from "../detail/port.ts";
import type { JudgePort, JudgeRefusal, JudgeRun } from "./port.ts";

export class FakeJudge implements JudgePort {
  private rows: { providerId: string; requestId: string; run: JudgeRun }[] = [];

  add(providerId: string, requestId: string, run: JudgeRun): void {
    this.rows.push({ providerId, requestId, run });
  }

  async runs(actor: Actor, requestId: string): Promise<Result<JudgeRun[], JudgeRefusal>> {
    if (actor.role === "viewer") return { ok: false, reason: "denied" };
    return { ok: true, value: this.rows.filter((r) => r.providerId === actor.providerId && r.requestId === requestId).map((r) => r.run) };
  }
}
