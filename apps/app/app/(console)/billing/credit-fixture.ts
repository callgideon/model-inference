/**
 * An in-memory `CreditReads` for the development preview and the U1R tests. It pages exactly as the
 * SQL does — jobs `created_at desc, request_id asc` (consumer_jobs), ledger `created_at desc,
 * entry_id desc` (credit_ledger_wallet_created_idx) — and its numbers reconcile: the wallet total is
 * its ledger, reserved is its active holds, and every settled CREDIT charge is one debit.
 *
 * Development and tests only: the pages reach it only through `creditSource` below, behind the same
 * production-build gate as the console preview (`../usage/fake-console-context.ts`).
 */

import { addCredit, subCredit, totalCredit, ZERO_CREDIT, type Credit } from "../../../lib/contracts/v2/money-units.ts";
import type { Page, Result } from "../../../lib/contracts/types.ts";
import orgsFixture from "../../../lib/contracts/fixtures/orgs.json" with { type: "json" };
import { previewAllowed } from "../usage/fake-console-context.ts";
import type {
  ConsumerJob,
  CreditLedgerEntry,
  CreditReads,
  CreditWallet,
  LegacyUsd,
  PageRequest,
} from "./credit-reads.ts";

export type CreditFixture = {
  wallet: CreditWallet | null;
  ledger: CreditLedgerEntry[];
  jobs: ConsumerJob[];
  legacy: LegacyUsd;
};

const MODEL = "nemostation/marlin-2b";
const REVISION = "nemostation/marlin-2b@2026-09-01";
const WALLET = "a1000000-0000-4000-8000-00000000000a";

function job(n: number, createdAt: string, over: Partial<ConsumerJob>): ConsumerJob {
  return {
    requestId: `b1000000-0000-4000-8000-00000000000${n}`,
    createdAt,
    requestedModel: MODEL,
    modelRevision: REVISION,
    executionMode: "async",
    state: "succeeded",
    outcomeCause: "completed",
    regime: "credit",
    unit: "CREDIT",
    settlementState: "settled",
    usageCertainty: "authoritative",
    promptTokens: 1200,
    completionTokens: 340,
    hold: "5.00000000",
    holdState: "settled",
    charged: null,
    resultAvailable: false,
    resultExpiresAt: null,
    ...over,
  };
}

/** A wallet mid-flight: settled, pending, unknown, free, absorbed, cancelled and legacy USD jobs. */
export function defaultCreditFixture(): CreditFixture {
  const jobs: ConsumerJob[] = [
    job(1, "2026-09-20T11:00:00.000000Z", { charged: "1.23456789", resultAvailable: true, resultExpiresAt: "2026-09-20T12:10:00.000000Z" }),
    job(2, "2026-09-20T11:00:00.000000Z", { charged: "0.98765432", executionMode: "sync" }),
    job(3, "2026-09-20T11:30:00.000000Z", { state: "running", outcomeCause: null, settlementState: null, usageCertainty: null, promptTokens: null, completionTokens: null, holdState: "held" }),
    job(4, "2026-09-20T11:20:00.000000Z", { state: "failed", outcomeCause: "engine_incomplete", settlementState: "held_unknown", usageCertainty: "unknown", promptTokens: null, completionTokens: null, holdState: "unknown" }),
    job(5, "2026-09-20T11:10:00.000000Z", { state: "failed", outcomeCause: "invalid_media", settlementState: "released_free", usageCertainty: null, promptTokens: null, completionTokens: null, holdState: "released" }),
    job(6, "2026-09-20T11:05:00.000000Z", { state: "failed", outcomeCause: "sync_deadline", settlementState: "released_platform_absorbed", usageCertainty: null, promptTokens: null, completionTokens: null, holdState: "released", executionMode: "sync" }),
    job(7, "2026-09-20T10:30:00.000000Z", { state: "cancelled", outcomeCause: "client_cancelled", charged: "0.10000000", promptTokens: 1200, completionTokens: 20, executionMode: "stream" }),
    job(8, "2026-09-19T12:00:00.000000Z", { regime: "legacy_usd", unit: "USD", charged: "0.00019660", hold: "0.00500000", holdState: "settled", modelRevision: MODEL }),
  ];
  const debits: CreditLedgerEntry[] = jobs
    .filter((j) => j.unit === "CREDIT" && j.settlementState === "settled" && j.charged !== null)
    .map((j, n) => ({
      id: `e1000000-0000-4000-8000-0000000000d${n}`,
      createdAt: j.createdAt.replace(":00.000000Z", ":05.000000Z"),
      kind: "inference_debit",
      amount: subCredit(ZERO_CREDIT, j.charged as Credit),
      requestId: j.requestId,
      reason: "inference",
      actor: "platform",
    }));
  const ledger: CreditLedgerEntry[] = [
    { id: "e1000000-0000-4000-8000-0000000000a1", createdAt: "2026-09-20T09:00:00.000000Z", kind: "signup_grant", amount: "10000.00000000" as Credit, requestId: null, reason: "", actor: "platform" },
    { id: "e1000000-0000-4000-8000-0000000000a2", createdAt: "2026-09-20T10:00:00.000000Z", kind: "operator_adjustment", amount: "-5.00000000" as Credit, requestId: null, reason: "Correction of a duplicated test grant", actor: "platform" },
    ...debits,
  ];
  const ledgerTotal = totalCredit(ledger.map((e) => e.amount));
  const reservedTotal = totalCredit(
    jobs.filter((j) => j.unit === "CREDIT" && (j.holdState === "held" || j.holdState === "unknown")).map((j) => j.hold as Credit),
  );
  return {
    wallet: {
      walletId: WALLET,
      orgId: "0a000000-0000-4000-8000-0000000000aa",
      ledgerTotal,
      reservedTotal,
      available: subCredit(ledgerTotal, reservedTotal),
      signupGrantedAt: "2026-09-20T09:00:00.000000Z",
    },
    ledger,
    jobs,
    legacy: { balance: "4.99980340" as LegacyUsd["balance"], entryCount: 2, rolloutHold: true },
  };
}

function ok<T>(value: T): Promise<Result<T>> {
  return Promise.resolve({ ok: true, value });
}

/** Keyset paging over a list already in page order; the cursor is the last row's key. */
function page<T>(items: T[], key: (item: T) => string, request: PageRequest): Page<T> {
  const start = request.cursor === null ? 0 : items.findIndex((item) => key(item) === request.cursor) + 1;
  const shown = items.slice(start, start + request.limit);
  const more = start + request.limit < items.length;
  return { items: shown, next_cursor: more && shown.length > 0 ? key(shown[shown.length - 1]) : null };
}

export function fixtureCreditReads(fixture: CreditFixture = defaultCreditFixture()): CreditReads {
  const jobs = [...fixture.jobs].sort((a, b) =>
    a.createdAt === b.createdAt ? (a.requestId < b.requestId ? -1 : 1) : a.createdAt < b.createdAt ? 1 : -1,
  );
  const ledger = [...fixture.ledger].sort((a, b) =>
    a.createdAt === b.createdAt ? (a.id < b.id ? 1 : -1) : a.createdAt < b.createdAt ? 1 : -1,
  );
  return {
    wallet: () => ok(fixture.wallet),
    ledger: (_walletId, request) => ok(page(ledger, (e) => `${e.createdAt}|${e.id}`, request)),
    creditsIn: () =>
      ok(ledger.filter((e) => e.kind !== "inference_debit").reduce((sum, e) => addCredit(sum, e.amount), ZERO_CREDIT)),
    legacyUsd: () => ok(fixture.legacy),
    jobs: (request) => ok(page(jobs, (j) => `${j.createdAt}|${j.requestId}`, request)),
  };
}

export type CreditSource = { reads: CreditReads; preview: boolean; now: Date };

/**
 * The one place the Usage and Credits pages' data source is chosen: the fixture only while the
 * console preview gate is open (development with an explicit opt-in, never a production build),
 * otherwise the caller's real session reads. Pure, so the gate has tests (U1R-G01..G03).
 */
export async function creditSource(
  real: () => Promise<CreditSource>,
  env: { NODE_ENV?: string; INFRX_CONSOLE_PREVIEW?: string } = process.env,
): Promise<CreditSource> {
  if (previewAllowed(env)) {
    return { reads: fixtureCreditReads(), preview: true, now: new Date(orgsFixture.clock) };
  }
  return real();
}
