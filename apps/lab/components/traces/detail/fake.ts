// V2: an in-memory stand-in for lab-api's trace read, for tests/v. It enforces what the read must:
// developer+ only (a viewer holds aggregate health only), another provider's request or any other id
// is not_found. Fixtures are provider-owned (no customer content).
import type { Actor, Result, TraceDetail, TraceReadPort, TraceRefusal } from "./port.ts";

type Row = { providerId: string; detail: TraceDetail };

export class FakeTraces implements TraceReadPort {
  private rows: Row[] = [];

  add(providerId: string, detail: TraceDetail): void {
    this.rows.push({ providerId, detail });
  }

  private find(actor: Actor, requestId: string): Row | undefined {
    return this.rows.find((r) => r.detail.request_id === requestId && r.providerId === actor.providerId);
  }

  async detail(actor: Actor, requestId: string): Promise<Result<TraceDetail, TraceRefusal>> {
    if (actor.role === "viewer") return { ok: false, reason: "denied" };
    const row = this.find(actor, requestId);
    return row === undefined ? { ok: false, reason: "not_found" } : { ok: true, value: row.detail };
  }
}
