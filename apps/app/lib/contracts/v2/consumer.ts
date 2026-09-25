/**
 * The consumer App's context and read port (C0, promoted by WR-3 so App/U lanes import the port's
 * types from the contract, not from `lib/services`). `lib/services/console.ts` implements them
 * (`resolveConsumerContext`, `createConsumerReads`, `consumerSessionFrom`, `consoleShell`) and
 * re-exports them for its existing importers.
 *
 * Type-only: nothing here runs. Owned by the coordinator: a change here is a contract revision.
 */

import type { ApiKeySummary, Page, PageQuery, Result } from "../types.ts";
import type { LegacyUsdStatement, BalanceV2, LedgerEntryKindV2 } from "./types.ts";
import type { AccountingRegime, Amount, Credit } from "./money-units.ts";
import type { ReadOutcome } from "./lifecycle.ts";

/** The part of a GoTrue user the context reads. `email_confirmed_at` is GoTrue's verification. */
export type AuthUser = { id: string; email?: string | null; email_confirmed_at?: string | null };

/** The individual's own consumer account: their wallet and the personal organization it funds. */
export type ConsumerAccount = {
  userId: string;
  email: string;
  walletId: string;
  orgId: string;
  /** R33: a suspended (or retired) individual keeps every read; new work is refused elsewhere. */
  suspended: boolean;
};

/**
 * Where a request stands. `onboarding` is a verified individual whose grant has not been issued
 * (C3A/A2 own the retry); `unavailable` is any failure to find out - never read as onboarding, which
 * would offer a grant flow to someone who has one, nor as signed out.
 */
export type ConsumerContext =
  | { state: "signed_out" }
  | { state: "unverified"; userId: string; email: string }
  | { state: "onboarding"; userId: string; email: string }
  | { state: "ready"; account: ConsumerAccount }
  | { state: "unavailable" };

/** One consumer CREDIT ledger entry. The unit is CREDIT by construction (the view carries only it). */
export type CreditLedgerEntry = {
  entry_id: string;
  created_at: string;
  kind: LedgerEntryKindV2;
  amount: Credit;
  request_id: string | null;
  reason: string;
};

/**
 * One of the individual's requests (D10 `consumer_jobs`). Money is in the row's OWN unit - CREDIT for
 * a credit job, USD for a legacy one - and is never summed across units or converted. `charged` is
 * `null` until the request is settled: an unsettled or uncertain charge is unknown, not zero.
 * `result` is F2C.b's ReadOutcome from the DB's persisted expiry, never the page's clock.
 */
export type ConsumerRequest = {
  request_id: string;
  created_at: string;
  model: string;
  model_revision: string | null;
  execution_mode: string | null;
  state: string;
  outcome_cause: string | null;
  accounting_regime: AccountingRegime;
  unit: "CREDIT" | "USD";
  hold: Amount | null;
  hold_state: string | null;
  charged: Amount | null;
  settlement_state: string | null;
  usage_certainty: string | null;
  usage: { prompt_tokens: number; completion_tokens: number } | null;
  result: ReadOutcome;
  result_expires_at: string | null;
  settled_at: string | null;
};

export type ConsumerReads = {
  balance(): Promise<Result<BalanceV2>>;
  /** `null`: this personal organization has no legacy USD history. Never merged into `balance`. */
  legacyUsd(): Promise<Result<LegacyUsdStatement | null>>;
  ledger(query: PageQuery): Promise<Result<Page<CreditLedgerEntry>>>;
  requests(query: PageQuery): Promise<Result<Page<ConsumerRequest>>>;
  request(requestId: string): Promise<Result<ConsumerRequest>>;
  /** The owned result body while the persisted expiry allows it. Never log or cache it. */
  result(requestId: string): Promise<Result<string>>;
  keys(): Promise<Result<ApiKeySummary[]>>;
};

/** The consumer App's request context: who is asking, and - only for a ready account - their reads. */
export type ConsumerSession = { context: ConsumerContext; reads: ConsumerReads | null };

/** What the console shell (`app/(console)/layout.tsx`, WR-1) does with a request. */
export type ConsoleShell =
  | { kind: "redirect"; to: string }
  | { kind: "render"; email: string; reads: ConsumerReads | null }
  | { kind: "panel"; state: "unverified" | "onboarding" | "unavailable" };
