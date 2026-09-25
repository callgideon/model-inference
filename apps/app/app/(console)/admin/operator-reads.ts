/**
 * U3: what the operator page shows, read with the operator's OWN Supabase session (cookie JWT, RLS
 * on, no service key). The relations are the D1/D1R read surface, each guarded in SQL by
 * `public.is_operator()` (a consumer session reads only its own rows, or none):
 *
 * - `console_credit_wallets` (0008): consumer CREDIT wallets, most recently moved first;
 * - `console_admin_orgs` (0005): the owner and suspension state of those wallets' organizations;
 * - `operator_unknown_usage` (WR-U3-1, D10): requests whose usage is unknown (`held_unknown`),
 *   oldest first - the 24 h reconciliation queue, both regimes (a CREDIT one has no usage row, so
 *   `console_usage` cannot show it);
 * - `operator_wallet_drift` (WR-U3-1, D10): wallets whose summary differs from ledger and holds;
 * until WR-U3-1 lands, those two sections are unavailable, which is the truth;
 * - `operator_audit` (0005): the newest operator writes.
 *
 * Each section is its own `Result`: a failed read, or a row that is not exactly what the relation
 * promises (a number where an exact decimal string belongs, figures that do not reconcile), is
 * `dependency_unavailable` for that section. Never a zero, a guess or a fixture.
 *
 * Pure `.ts` with relative imports (R48): the client is injected, so node --test loads this.
 */

import { AUDIT_ACTIONS, type AuditAction, type Result } from "../../../lib/contracts/types.ts";
import { parseCredit, parseUsd, subCredit, type Credit, type Usd } from "../../../lib/contracts/v2/money-units.ts";

type Answer = { data: unknown; error: { code?: string | null; message?: string | null } | null };

export interface Query extends PromiseLike<Answer> {
  eq(column: string, value: string): Query;
  in(column: string, values: string[]): Query;
  order(column: string, options: { ascending: boolean }): Query;
  limit(count: number): Query;
}

/** The part of a supabase-js client these reads call; the cookie client fits. */
export type ReadClient = { from(relation: string): { select(columns: string): Query } };

export const ACCOUNT_LIMIT = 100;
export const UNKNOWN_LIMIT = 50;
export const DRIFT_LIMIT = 50;
export const AUDIT_LIMIT = 25;

export const OPERATOR_RELATIONS = [
  "console_credit_wallets",
  "console_admin_orgs",
  "operator_unknown_usage",
  "operator_wallet_drift",
  "operator_audit",
] as const;

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
  /** When the database clock allows the 24 h release (never a debit). */
  reconcileAfter: string;
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

/** A row that is not what the relation promises. Caught per section. */
class Malformed extends Error {}

function str(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (typeof value !== "string" || value === "") throw new Malformed(key);
  return value;
}

function optional(row: Record<string, unknown>, key: string): string | null {
  return row[key] === null || row[key] === undefined ? null : str(row, key);
}

function credit(row: Record<string, unknown>, key: string): Credit {
  try {
    return parseCredit(row[key]);
  } catch {
    throw new Malformed(key);
  }
}

async function rows(query: PromiseLike<Answer>): Promise<Record<string, unknown>[]> {
  const { data, error } = await query;
  if (error !== null || !Array.isArray(data)) throw new Malformed("read");
  return data as Record<string, unknown>[];
}

async function section<T>(read: () => Promise<T>): Promise<Result<T>> {
  try {
    return { ok: true, value: await read() };
  } catch {
    return { ok: false, error: { code: "dependency_unavailable", message: UNAVAILABLE } };
  }
}

async function accounts(client: ReadClient): Promise<OperatorAccount[]> {
  const wallets = await rows(
    client
      .from("console_credit_wallets")
      .select("wallet_id,owner_user_id,org_id,ledger_total,reserved_total,available,signup_granted_at")
      .eq("kind", "consumer")
      .order("updated_at", { ascending: false })
      .limit(ACCOUNT_LIMIT),
  );
  if (wallets.length === 0) return [];
  const ids = [...new Set(wallets.map((w) => str(w, "org_id")))];
  const orgs = new Map(
    (await rows(client.from("console_admin_orgs").select("org_id,owner_email,suspended,suspension_reason").in("org_id", ids))).map(
      (o) => [str(o, "org_id"), o] as const,
    ),
  );
  return wallets.map((w) => {
    const org = orgs.get(str(w, "org_id"));
    if (org === undefined || typeof org.suspended !== "boolean") throw new Malformed("organization");
    const ledgerTotal = credit(w, "ledger_total");
    const reservedTotal = credit(w, "reserved_total");
    const available = credit(w, "available");
    if (subCredit(ledgerTotal, reservedTotal) !== available) throw new Malformed("available");
    return {
      userId: str(w, "owner_user_id"),
      orgId: str(w, "org_id"),
      walletId: str(w, "wallet_id"),
      email: optional(org, "owner_email"),
      suspended: org.suspended,
      suspensionReason: optional(org, "suspension_reason"),
      ledgerTotal,
      reservedTotal,
      available,
      signupGrantedAt: optional(w, "signup_granted_at"),
    };
  });
}

async function unknownUsage(client: ReadClient): Promise<UnknownUsage[]> {
  const held = await rows(
    client
      .from("operator_unknown_usage")
      .select("request_id,org_id,created_at,reconcile_after,unit,hold")
      .order("created_at", { ascending: true })
      .limit(UNKNOWN_LIMIT),
  );
  return held.map((r) => {
    let hold: UnknownUsage["hold"] = null;
    if (r.unit !== "CREDIT" && r.unit !== "USD") throw new Malformed("unit");
    if (r.hold !== null) {
      try {
        hold = r.unit === "CREDIT" ? { amount: parseCredit(r.hold), unit: "CREDIT" } : { amount: parseUsd(r.hold), unit: "USD" };
      } catch {
        throw new Malformed("hold");
      }
    }
    return {
      requestId: str(r, "request_id"),
      orgId: str(r, "org_id"),
      createdAt: str(r, "created_at"),
      reconcileAfter: str(r, "reconcile_after"),
      hold,
    };
  });
}

async function drift(client: ReadClient): Promise<WalletDrift[]> {
  const found = await rows(client.from("operator_wallet_drift").select("wallet_id,kind,ledger_drift,reserved_drift").limit(DRIFT_LIMIT));
  return found.map((r) => ({
    walletId: str(r, "wallet_id"),
    kind: str(r, "kind"),
    ledgerDrift: credit(r, "ledger_drift"),
    reservedDrift: credit(r, "reserved_drift"),
  }));
}

async function audit(client: ReadClient): Promise<OperatorAuditEntry[]> {
  const entries = await rows(
    client
      .from("operator_audit")
      .select("id,at,actor_principal,action,target_org_id,reason,idempotency_key")
      .order("at", { ascending: false })
      .limit(AUDIT_LIMIT),
  );
  return entries.map((r) => {
    const action = str(r, "action");
    if (!(AUDIT_ACTIONS as readonly string[]).includes(action)) throw new Malformed("action");
    return {
      id: str(r, "id"),
      at: str(r, "at"),
      actor: str(r, "actor_principal"),
      action: action as AuditAction,
      targetOrgId: optional(r, "target_org_id"),
      reason: str(r, "reason"),
      idempotencyKey: optional(r, "idempotency_key"),
    };
  });
}

export async function operatorReads(client: ReadClient): Promise<OperatorView> {
  const [a, u, d, t] = await Promise.all([
    section(() => accounts(client)),
    section(() => unknownUsage(client)),
    section(() => drift(client)),
    section(() => audit(client)),
  ]);
  return { accounts: a, unknownUsage: u, drift: d, audit: t };
}
