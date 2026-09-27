/**
 * The Lab catalog fake (server-side / test use: it hashes with node:crypto), mirroring
 * `infrx/contracts/lab/fakes.py`: content-addressed, immutable, provider-scoped.
 */
import { createHash } from "node:crypto";

import { REFERABLE, type Schema, validate } from "./index.ts";

/**
 * RFC 8785 (JCS): keys by UTF-16 code unit, no whitespace, ECMAScript number spelling
 * (`JSON.stringify`); the same bytes as Python's `records.canonical`.
 */
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

/** The immutable ref of a valid referable record. Throws on anything `validate` refuses. */
export function refOf(payload: Record<string, unknown>): string {
  const reason = validate(payload);
  const referable = REFERABLE[payload.schema as Schema];
  if (reason !== null || referable === undefined) throw new Error(`not a referable Lab record: ${reason}`);
  const [kind, idField] = referable;
  const digest = createHash("sha256").update(canonicalJson(payload), "utf8").digest("hex");
  return `lab:${kind}:${payload.provider_org_id}:${payload[idField]}@sha256:${digest}`;
}

export class FakeLabCatalog {
  private readonly rows = new Map<string, Record<string, unknown>>();

  publish(payload: Record<string, unknown>): string {
    const ref = refOf(payload);
    if (!this.rows.has(ref)) this.rows.set(ref, structuredClone(payload));
    return ref;
  }

  /** `providerOrgId` is the caller's server-derived provider; another's ref is not_found. */
  resolve(ref: string, providerOrgId: string): Record<string, unknown> {
    const row = this.rows.get(ref);
    if (row === undefined || row.provider_org_id !== providerOrgId) throw new Error("not_found");
    return structuredClone(row);
  }

  refs(): string[] {
    return [...this.rows.keys()].sort();
  }
}
