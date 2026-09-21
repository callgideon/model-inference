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
  DEFAULT_PAGE_LIMIT,
  PLATFORM_ACTOR,
  type LedgerEntry,
  type LedgerEntryKind,
  type Page,
  type PageQuery,
  type Result,
  type WalletBalance,
} from "../../../lib/contracts/types.ts";
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

/** The label for a kind this table does not know: a fact to render plainly, not a blank or a crash. */
export const UNKNOWN_KIND_LABEL = "Other";

export function ledgerKindLabel(kind: LedgerEntryKind): string {
  // `KIND_LABELS[kind]` alone answered `toString` with a *function* and an unrecognised kind with
  // `undefined`, and both went straight into the table as a React child. The kind comes from the
  // service, which is a boundary like any other.
  return Object.hasOwn(KIND_LABELS, kind) ? KIND_LABELS[kind] : UNKNOWN_KIND_LABEL;
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

// ---------------------------------------------------------------------------
// The page models: every branch the balance card and the ledger page take
// ---------------------------------------------------------------------------

/** One page of the ledger. The usage page only needs to know *whether* there is any history. */
export const LEDGER_PAGE_SIZE = DEFAULT_PAGE_LIMIT;
export const HISTORY_PROBE_LIMIT = 1;

export function ledgerPageQuery(state: PageCursor): PageQuery {
  return { limit: LEDGER_PAGE_SIZE, ...(state.cursor === null ? {} : { cursor: state.cursor }) };
}

export function historyProbeQuery(): PageQuery {
  return { limit: HISTORY_PROBE_LIMIT };
}

/**
 * Whether this organization has ledger history at all.
 *
 * A *failed* read is not evidence of absence: reporting "no history" from an error would greet an
 * established organization as brand new and tell it to go ask for its first grant. So a failure
 * counts as history, and being on a later page counts as history whatever this page holds.
 */
export function hasLedgerHistory(
  ledger: Result<Page<LedgerEntry>>,
  state: PageCursor = { cursor: null, trail: [] },
): boolean {
  if (!ledger.ok) return true;
  return ledger.value.items.length > 0 || state.cursor !== null;
}

export type BalanceCardModel = {
  figures: BalanceFigure[];
  state: BalanceState;
  /** False when the three figures do not satisfy the contract's identity; the card says so. */
  reconciles: boolean;
  notice: string;
};

export function balanceCardModel(balance: WalletBalance, hasHistory: boolean): BalanceCardModel {
  return {
    figures: balanceFigures(balance),
    state: balanceState(balance, hasHistory),
    reconciles: balanceIsConsistent(balance),
    notice: PROMOTIONAL_NOTICE,
  };
}

/**
 * The card as a state, so a failed `balances` read renders an error instead of quietly vanishing —
 * a missing card is indistinguishable from a card that has not loaded, and both read as "no money".
 */
export function balanceCardState(
  balance: Result<WalletBalance>,
  ledger: Result<Page<LedgerEntry>>,
  state: PageCursor = { cursor: null, trail: [] },
): ViewState<BalanceCardModel> {
  return mapState(viewStateOf(balance, () => false), (wallet) =>
    balanceCardModel(wallet, hasLedgerHistory(ledger, state)),
  );
}

export type LedgerPageRows = {
  rows: LedgerRowView[];
  page: number;
  firstHref: string;
  previousHref: string | null;
  nextHref: string | null;
};

export type BillingPageModel = {
  here: string;
  firstHref: string;
  balance: ViewState<BalanceCardModel>;
  ledger: ViewState<LedgerPageRows>;
};

export function billingPageModel(input: {
  state: PageCursor;
  balance: Result<WalletBalance>;
  ledger: Result<Page<LedgerEntry>>;
}): BillingPageModel {
  const firstHref = ledgerHref(firstCursorState(input.state));
  return {
    here: ledgerHref(input.state),
    firstHref,
    balance: balanceCardState(input.balance, input.ledger, input.state),
    ledger: mapState(
      viewStateOf(input.ledger, (value) => value.items.length === 0),
      (value) => ({
        rows: value.items.map(ledgerRowView),
        page: pageNumberOf(input.state),
        firstHref,
        previousHref: hasPreviousPage(input.state)
          ? ledgerHref(previousCursorState(input.state))
          : null,
        nextHref:
          value.next_cursor === null
            ? null
            : ledgerHref(nextCursorState(input.state, value.next_cursor)),
      }),
    ),
  };
}
