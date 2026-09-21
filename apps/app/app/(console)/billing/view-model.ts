/**
 * Promotional balance and ledger view model (U1).
 *
 * The pilot has no payments (DEC-01): this balance is credit the infrx team granted, it is not cash
 * revenue, there is nothing to buy and nothing to pay. Every string here says so, and no control
 * this file describes takes money.
 *
 * Pure `.ts` with relative imports, so `node --test` loads it (R48).
 */

import {
  displayMoney,
  isNegativeMoney,
  isZeroMoney,
  moneyUnits,
} from "../../../lib/contracts/money.ts";
import {
  PLATFORM_ACTOR,
  type LedgerEntry,
  type LedgerEntryKind,
  type WalletBalance,
} from "../../../lib/contracts/types.ts";
import { instantLabel } from "../usage/view-model.ts";

export const PROMOTIONAL_NOTICE =
  "Promotional pilot credit, granted by the infrx team. It is not a cash balance, it cannot be " +
  "refunded or withdrawn, and there is nothing here to pay.";

export type BalanceFigure = {
  label: string;
  value: string;
  hint: string;
  emphasis: boolean;
};

/**
 * Three figures, because "what I was given", "what is spoken for" and "what I can spend right now"
 * are three different questions, and a single "balance" answers none of them while a request is in
 * flight.
 */
export function balanceFigures(balance: WalletBalance): BalanceFigure[] {
  return [
    {
      label: "Available now",
      value: displayMoney(balance.available),
      hint: "Granted credit minus what in-flight requests are holding.",
      emphasis: true,
    },
    {
      label: "Promotional credit",
      value: displayMoney(balance.ledger_total),
      hint: "Every grant and adjustment, less everything already settled.",
      emphasis: false,
    },
    {
      label: "Reserved",
      value: displayMoney(balance.reserved_total),
      hint: "Held while requests run or await reconciliation. Released or charged when they settle.",
      emphasis: false,
    },
  ];
}

/**
 * The contract's own identity, `available = ledger_total - reserved` (08 §9). A page that shows all
 * three numbers can check it for free, and a mismatch is worth saying out loud: silently rendering
 * three numbers that do not add up teaches the reader to distrust the two that are right.
 */
export function balanceIsConsistent(balance: WalletBalance): boolean {
  return (
    moneyUnits(balance.available) === moneyUnits(balance.ledger_total) - moneyUnits(balance.reserved_total)
  );
}

export type BalanceState =
  | { kind: "new"; headline: string; guidance: string }
  | { kind: "exhausted"; headline: string; guidance: string }
  | { kind: "funded" };

const ASK_OPERATOR =
  "An infrx operator grants pilot credit — ask your infrx contact and it appears here. There is " +
  "no card to add and nothing to buy.";

/**
 * Three states, not two. A brand-new organization has never been granted anything; an organization
 * that has spent or reserved everything has a history and a different next step. Rendering both as
 * "$0.00" leaves a new customer with no idea what to do.
 */
export function balanceState(balance: WalletBalance, hasHistory: boolean): BalanceState {
  if (!hasHistory && isZeroMoney(balance.ledger_total)) {
    return {
      kind: "new",
      headline: "No promotional credit yet",
      guidance: ASK_OPERATOR,
    };
  }
  if (isZeroMoney(balance.available) || isNegativeMoney(balance.available)) {
    return {
      kind: "exhausted",
      headline: "No credit available",
      guidance: `Your grants are fully spent or reserved by requests in flight. ${ASK_OPERATOR}`,
    };
  }
  return { kind: "funded" };
}

const KIND_LABELS: Record<LedgerEntryKind, string> = {
  grant: "Promotional grant",
  usage: "Requests",
  adjustment: "Adjustment",
  // R13: nothing creates one any more, but a migrated row must still render as what it is.
  purchase: "Purchase (legacy)",
};

export function ledgerKindLabel(kind: LedgerEntryKind): string {
  return KIND_LABELS[kind];
}

/**
 * Who caused the entry, as this session may know it. `platform` is the literal the service returns
 * for anything an operator did (R41) — a tenant learns the platform acted and never which person
 * did it, so the label must not imply an individual.
 */
export function actorLabel(actor: string | null): string {
  if (actor === null) return "—";
  return actor === PLATFORM_ACTOR ? "infrx platform" : actor;
}

/** A credit reads as a credit. `displayMoney` already carries the minus sign for a debit. */
export function signedMoney(amount: LedgerEntry["delta"]): string {
  const shown = displayMoney(amount);
  return isNegativeMoney(amount) || isZeroMoney(amount) ? shown : `+${shown}`;
}

export type LedgerRowView = {
  id: string;
  when: string;
  kind: string;
  /** The grant reason is written for the customer and shown verbatim (R17, C README). */
  reason: string;
  actor: string;
  amount: string;
  credit: boolean;
};

export function ledgerRowView(entry: LedgerEntry): LedgerRowView {
  return {
    id: entry.id,
    when: instantLabel(entry.created_at),
    kind: ledgerKindLabel(entry.kind),
    reason: entry.reason ?? "—",
    actor: actorLabel(entry.actor),
    amount: signedMoney(entry.delta),
    credit: !isNegativeMoney(entry.delta),
  };
}
