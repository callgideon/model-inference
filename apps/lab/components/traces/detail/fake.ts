// V2: an in-memory stand-in for lab-api's trace read and C2's content refs, for tests/v.
// It enforces what they must: developer+ only (a viewer holds aggregate health
// only), another provider's request or any other id is not_found, a revoked grant or an
// expired ref never yields content. Fixtures are provider-owned (no customer content).
import type { Actor, Content, ContentPort, ContentRefusal, Result, TraceDetail, TraceReadPort, TraceRefusal } from "./port.ts";

type Row = { providerId: string; detail: TraceDetail; text: string | null; revoked: boolean; refExpired: boolean };

export class FakeTraces implements TraceReadPort, ContentPort {
  readonly reads: unknown[][] = [];
  private rows: Row[] = [];

  add(providerId: string, detail: TraceDetail, text: string | null = null): Row {
    const row = { providerId, detail, text, revoked: false, refExpired: false };
    this.rows.push(row);
    return row;
  }

  private find(actor: Actor, requestId: string): Row | undefined {
    return this.rows.find((r) => r.detail.request_id === requestId && r.providerId === actor.providerId);
  }

  async detail(actor: Actor, requestId: string): Promise<Result<TraceDetail, TraceRefusal>> {
    if (actor.role === "viewer") return { ok: false, reason: "denied" };
    const row = this.find(actor, requestId);
    return row === undefined ? { ok: false, reason: "not_found" } : { ok: true, value: row.detail };
  }

  async read(actor: Actor, grantRef: string, requestId: string): Promise<Result<Content, ContentRefusal>> {
    this.reads.push([actor, grantRef, requestId]);
    const row = actor.role === "viewer" ? undefined : this.find(actor, requestId);
    if (row === undefined) return { ok: false, reason: "not_found" };
    const d = row.detail;
    if (d.access !== "content" || d.grant_ref !== grantRef || row.revoked) return { ok: false, reason: "forbidden" };
    if (row.refExpired || !d.content_available || row.text === null) return { ok: false, reason: "expired" };
    return { ok: true, value: { text: row.text, expiresAt: new Date(Date.now() + 5 * 60_000).toISOString() } };
  }
}
