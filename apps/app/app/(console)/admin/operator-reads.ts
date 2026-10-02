/**
 * U3: what the operator page shows, read through infrx-api's operator projections (AP-09 09a) as
 * the signed-in operator - the API checks operator authority on every call (a consumer session is
 * 403), so nothing here decides who may look:
 *
 * - `GET /operator/v1/accounts`: consumer CREDIT wallets with their owner and suspension state;
 * - `GET /operator/v1/unknown-usage`: requests whose usage is unknown (`held_unknown`), the 24 h
 *   reconciliation queue, both regimes;
 * - `GET /operator/v1/wallet-drift`: wallets whose summary differs from ledger and holds;
 * - `GET /operator/v1/audit`: the newest operator writes.
 *
 * Each section is its own `Result`: a failed read, or a document that is not exactly what the
 * contract promises (a unit that is not the section's, an action outside the audit vocabulary), is
 * `dependency_unavailable` for that section. Never a zero, a guess or a fixture.
 *
 * Pure `.ts` with relative imports (R48): the client is injected, so node --test loads this.
 */

import type { ConsumerApi } from "../../../lib/api/index.ts";
import { credit, instant, Malformed, optionalInstant, usd } from "../../../lib/api/result.ts";
import { AUDIT_ACTIONS, type AuditAction, type Result } from "../../../lib/contracts/types.ts";
import type { Credit, Usd } from "../../../lib/contracts/v2/money-units.ts";

export const ACCOUNT_LIMIT = 100;
export const UNKNOWN_LIMIT = 50;
export const DRIFT_LIMIT = 50;
export const AUDIT_LIMIT = 25;

export type OperatorAccount = {
  userId: string;
  orgId: string;
  walletId: string;
  email: string | null;
  suspended: boolean;
  suspensionReason: string | null;
  ledgerTotal: Credit;
  reservedTotal: Credit;
  available: Credit;
  signupGrantedAt: string | null;
};

export type UnknownUsage = {
  requestId: string;
  orgId: string;
  createdAt: string;
  /** When the database clock allows the 24 h release (never a debit); null if the API cannot say. */
  reconcileAfter: string | null;
  /** The quarantined hold in the job's own unit; null once released. Never charged. */
  hold: { amount: Credit; unit: "CREDIT" } | { amount: Usd; unit: "USD" } | null;
};

export type WalletDrift = { walletId: string; kind: string; ledgerDrift: Credit; reservedDrift: Credit };

export type OperatorAuditEntry = {
  id: string;
  at: string;
  actor: string;
  action: AuditAction;
  targetOrgId: string | null;
  reason: string;
  idempotencyKey: string | null;
};

export type OperatorView = {
  accounts: Result<OperatorAccount[]>;
  unknownUsage: Result<UnknownUsage[]>;
  drift: Result<WalletDrift[]>;
  audit: Result<OperatorAuditEntry[]>;
};

const UNAVAILABLE = "this could not be read right now; reload to try again";

type Paged<W> = Promise<{ ok: true; data: { data: W[] } } | { ok: false }>;

async function section<W, T>(call: Paged<W>, map: (row: W) => T): Promise<Result<T[]>> {
  try {
    const answer = await call;
    if (!answer.ok) throw new Malformed("read");
    return { ok: true, value: answer.data.data.map(map) };
  } catch {
    return { ok: false, error: { code: "dependency_unavailable", message: UNAVAILABLE } };
  }
}

export async function operatorReads(api: ConsumerApi): Promise<OperatorView> {
  const [accounts, unknownUsage, drift, audit] = await Promise.all([
    section(api.call("get", "/operator/v1/accounts", { query: { limit: ACCOUNT_LIMIT } }), (a): OperatorAccount => ({
      userId: a.user_id,
      orgId: a.org_id,
      walletId: a.wallet_id,
      email: a.email,
      suspended: a.suspended,
      suspensionReason: a.suspension_reason,
      ledgerTotal: credit(a.ledger_total),
      reservedTotal: credit(a.reserved_total),
      available: credit(a.available),
      signupGrantedAt: optionalInstant(a.signup_granted_at),
    })),
    section(api.call("get", "/operator/v1/unknown-usage", { query: { limit: UNKNOWN_LIMIT } }), (u): UnknownUsage => ({
      requestId: u.request_id,
      orgId: u.org_id,
      createdAt: instant(u.created_at),
      reconcileAfter: optionalInstant(u.reconcile_after),
      hold: u.hold === null ? null : u.hold.unit === "USD" ? { amount: usd(u.hold), unit: "USD" } : { amount: credit(u.hold), unit: "CREDIT" },
    })),
    section(api.call("get", "/operator/v1/wallet-drift", { query: { limit: DRIFT_LIMIT } }), (d): WalletDrift => ({
      walletId: d.wallet_id,
      kind: d.kind,
      ledgerDrift: credit(d.ledger_drift),
      reservedDrift: credit(d.reserved_drift),
    })),
    section(api.call("get", "/operator/v1/audit", { query: { limit: AUDIT_LIMIT } }), (e): OperatorAuditEntry => {
      if (!(AUDIT_ACTIONS as readonly string[]).includes(e.action)) throw new Malformed("action");
      return {
        id: e.id,
        at: instant(e.at),
        actor: e.actor,
        action: e.action as AuditAction,
        targetOrgId: e.target_org_id,
        reason: e.reason,
        idempotencyKey: e.idempotency_key,
      };
    }),
  ]);
  return { accounts, unknownUsage, drift, audit };
}
