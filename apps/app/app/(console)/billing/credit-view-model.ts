/**
 * Credits page view model (U1R): the consumer's CREDIT wallet, its ledger and the separate legacy
 * USD statement. Every string the page shows is decided here; the `.tsx` files are markup.
 *
 * - Amounts are exact decimal strings shown through `lib/format.ts`: credits never carry "$", legacy
 *   USD always says USD, and the two are never added (02-credits; no conversion rate exists, P-02).
 * - The signup grant is one-time: nothing here promises a refill, an expiry or a purchase.
 * - A failed read is an error state with its recovery, never a zero and never an empty ledger.
 *
 * Pure `.ts` with relative imports, so `node --test` loads it (R48).
 */

import { credits, signedAmount, usd } from "../../../lib/format.ts";
import {
  compareCredit,
  subCredit,
  ZERO_CREDIT,
  type Credit,
} from "../../../lib/contracts/v2/money-units.ts";
import type { Page, Result } from "../../../lib/contracts/types.ts";
import {
  spentCredit,
  type CreditLedgerEntry,
  type CreditWallet,
  type LegacyUsd,
} from "./credit-reads.ts";
import {
  firstCursorState,
  hasPreviousPage,
  instantLabel,
  ledgerHref,
  mapState,
  nextCursorState,
  pageNumberOf,
  previousCursorState,
  viewStateOf,
  type PageCursor,
  type ViewState,
} from "../usage/view-model.ts";

export const CREDITS_NOTICE =
  "Credits are a product unit, not money. Your one-time 10,000 credit grant is not refilled and " +
  "does not expire, and this release takes no payments.";

/**
 * Below this many available credits the page warns before the API starts refusing.
 *
 * ponytail: a display threshold (10% of the one-time grant), not policy — admission refuses only
 * when a request's own maximum hold does not fit. Replace with "fewer than N requests at the current
 * rate" once the approved rate card (P-01) is readable here.
 */
export const LOW_FUNDS = "1000.00000000" as Credit;

/** Where a request's detail lives (U4 owns the page; the route is requested from the coordinator). */
export function requestDetailHref(requestId: string): string {
  return `/usage/${encodeURIComponent(requestId)}`;
}

// ---------------------------------------------------------------------------
// The balance card
// ---------------------------------------------------------------------------

export type CreditFigure = { label: string; value: string; hint: string; emphasis: boolean };

const UNAVAILABLE = "Unavailable";

export function creditFigures(wallet: CreditWallet, spent: Credit | null): CreditFigure[] {
  return [
    {
      label: "Available",
      value: credits(wallet.available),
      hint: "Spendable now: your balance minus what running requests hold.",
      emphasis: true,
    },
    {
      label: "Reserved",
      value: credits(wallet.reservedTotal),
      hint: "Held for requests in progress or awaiting reconciliation. Charged or released when they settle; not spent.",
      emphasis: false,
    },
    {
      label: "Spent",
      value: spent === null ? UNAVAILABLE : credits(spent),
      hint:
        spent === null
          ? "Could not be totalled just now. The other figures are exact."
          : "Charged for settled requests.",
      emphasis: false,
    },
    {
      label: "Balance",
      value: credits(wallet.ledgerTotal),
      hint: "Your grant and any adjustments, less settled charges.",
      emphasis: false,
    },
  ];
}

/** The ledger identity (R59-7): available = balance − reserved. A mismatch is said, not hidden. */
export function walletReconciles(wallet: CreditWallet): boolean {
  return subCredit(wallet.ledgerTotal, wallet.reservedTotal) === wallet.available;
}

export type CreditAccountState =
  | { kind: "no_wallet"; headline: string; guidance: string }
  | { kind: "exhausted"; headline: string; guidance: string }
  | { kind: "low"; headline: string; guidance: string }
  | { kind: "funded" };

const REFUSAL = "the API refuses new requests with HTTP 402 insufficient_credit";

export function creditAccountState(wallet: CreditWallet | null): CreditAccountState {
  if (wallet === null) {
    return {
      kind: "no_wallet",
      headline: "No credits yet",
      guidance:
        "Your one-time 10,000 credit grant is issued once your email address is verified. If you " +
        "have verified and still see this, sign out and back in, or contact the infrx team.",
    };
  }
  if (compareCredit(wallet.available, ZERO_CREDIT) <= 0) {
    return {
      kind: "exhausted",
      headline: "No credits available",
      guidance:
        `Your one-time grant is spent or held by requests in progress, so ${REFUSAL} until a hold ` +
        "is released. Credits are not refilled.",
    };
  }
  if (compareCredit(wallet.available, LOW_FUNDS) < 0) {
    return {
      kind: "low",
      headline: "Credits running low",
      guidance:
        `${credits(wallet.available)} left. A request is admitted only when its maximum hold fits ` +
        `in what is available; otherwise ${REFUSAL}. Credits are not refilled.`,
    };
  }
  return { kind: "funded" };
}

export function grantLine(wallet: CreditWallet): string {
  return wallet.signupGrantedAt === null
    ? "One-time 10,000 credit signup grant: not received yet."
    : `One-time signup grant of 10,000 credits, received ${instantLabel(wallet.signupGrantedAt)}.`;
}

export type CreditCardModel = {
  /** Empty when there is no wallet: a new account is not three zeroes. */
  figures: CreditFigure[];
  grant: string;
  state: CreditAccountState;
  reconciles: boolean;
  notice: string;
};

/**
 * The card as a state. `creditsIn` is null when it was not read; its failure only makes "Spent"
 * unavailable, because available and reserved come from the wallet row itself.
 */
export function creditCardState(
  wallet: Result<CreditWallet | null>,
  creditsIn: Result<Credit | null> | null,
): ViewState<CreditCardModel> {
  return mapState(viewStateOf(wallet, () => false), (found) => {
    if (found === null) {
      return { figures: [], grant: "", state: creditAccountState(null), reconciles: true, notice: CREDITS_NOTICE };
    }
    const spent = creditsIn !== null && creditsIn.ok ? spentCredit(found, creditsIn.value) : null;
    return {
      figures: creditFigures(found, spent),
      grant: grantLine(found),
      state: creditAccountState(found),
      reconciles: walletReconciles(found),
      notice: CREDITS_NOTICE,
    };
  });
}

// ---------------------------------------------------------------------------
// The ledger
// ---------------------------------------------------------------------------

const KIND_LABELS: Record<string, string> = {
  signup_grant: "One-time signup grant",
  operator_adjustment: "Adjustment",
  operator_allocation: "Allocation",
  inference_debit: "Request charge",
};

export type LedgerEntryView = {
  id: string;
  when: string;
  kind: string;
  reason: string;
  amount: string;
  detailHref: string | null;
};

export function ledgerEntryView(entry: CreditLedgerEntry): LedgerEntryView {
  return {
    id: entry.id,
    when: instantLabel(entry.createdAt),
    kind: Object.hasOwn(KIND_LABELS, entry.kind) ? KIND_LABELS[entry.kind] : "Other",
    reason: entry.reason === "" ? "—" : entry.reason,
    amount: signedAmount(entry.amount, "CREDIT"),
    detailHref: entry.requestId === null ? null : requestDetailHref(entry.requestId),
  };
}

// ---------------------------------------------------------------------------
// Legacy USD
// ---------------------------------------------------------------------------

export type LegacyUsdView = { balance: string; entries: number; text: string; hold: string | null };

/** Shown only when USD history exists. `null` means it was not read (no wallet, no org). */
export function legacyUsdState(result: Result<LegacyUsd> | null): ViewState<LegacyUsdView> {
  if (result === null) return { kind: "empty" };
  return mapState(viewStateOf(result, (value) => value.entryCount === 0), (value) => ({
    balance: usd(value.balance),
    entries: value.entryCount,
    text:
      "Pilot history in US dollars, kept separately. It is not credits, it is not converted, and " +
      "it cannot be spent here.",
    hold: value.rolloutHold
      ? "This balance is kept on hold until the pilot transition is decided; it is not discarded."
      : null,
  }));
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

export const LEDGER_PAGE_SIZE = 25;

export type LedgerRows = {
  rows: LedgerEntryView[];
  page: number;
  firstHref: string;
  previousHref: string | null;
  nextHref: string | null;
};

export type CreditsPageModel = {
  here: string;
  firstHref: string;
  card: ViewState<CreditCardModel>;
  ledger: ViewState<LedgerRows>;
  legacy: ViewState<LegacyUsdView>;
};

export function creditsPageModel(input: {
  state: PageCursor;
  wallet: Result<CreditWallet | null>;
  creditsIn: Result<Credit | null> | null;
  /** null when not read: there is no wallet to read it for. */
  ledger: Result<Page<CreditLedgerEntry>> | null;
  legacy: Result<LegacyUsd> | null;
}): CreditsPageModel {
  const { state } = input;
  const firstHref = ledgerHref(firstCursorState(state));
  return {
    here: ledgerHref(state),
    firstHref,
    card: creditCardState(input.wallet, input.creditsIn),
    ledger:
      input.ledger === null
        ? // Not read. With no wallet that is "no entries"; after a failed wallet read it is that error.
          input.wallet.ok
          ? { kind: "empty" }
          : (viewStateOf(input.wallet, () => true) as ViewState<LedgerRows>)
        : mapState(viewStateOf(input.ledger, (page) => page.items.length === 0), (page) => ({
            rows: page.items.map(ledgerEntryView),
            page: pageNumberOf(state),
            firstHref,
            previousHref: hasPreviousPage(state) ? ledgerHref(previousCursorState(state)) : null,
            nextHref: page.next_cursor === null ? null : ledgerHref(nextCursorState(state, page.next_cursor)),
          })),
    legacy: legacyUsdState(input.legacy),
  };
}
