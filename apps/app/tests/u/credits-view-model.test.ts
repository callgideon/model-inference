// node --test "tests/**/*.test.ts"
//
// U1R Credits page view model (`app/(console)/billing/credit-view-model.ts`). The balance is exact
// CREDIT, labelled as credits, three figures that reconcile; the grant is one-time; a failed read
// is an explicit error and never a zero; legacy USD is its own labelled section, never summed.
import assert from "node:assert/strict";
import test from "node:test";

import type { Credit } from "../../lib/contracts/v2/money-units.ts";
import type { Page, Result } from "../../lib/contracts/types.ts";
import type {
  CreditLedgerEntry,
  CreditWallet,
  LegacyUsd,
} from "../../app/(console)/billing/credit-reads.ts";
import {
  CREDITS_NOTICE,
  LOW_FUNDS,
  creditAccountState,
  creditCardState,
  creditsPageModel,
  ledgerEntryView,
  legacyUsdState,
} from "../../app/(console)/billing/credit-view-model.ts";

const WALLET = "a1000000-0000-4000-8000-00000000000a";

function wallet(total: string, reserved: string, available?: string): CreditWallet {
  const units = (v: string) => BigInt(v.replace(".", ""));
  const fmt = (u: bigint) => {
    const neg = u < BigInt(0);
    const s = (neg ? -u : u).toString().padStart(9, "0");
    return `${neg ? "-" : ""}${s.slice(0, -8)}.${s.slice(-8)}`;
  };
  return {
    walletId: WALLET,
    orgId: "0a000000-0000-4000-8000-0000000000aa",
    ledgerTotal: total as Credit,
    reservedTotal: reserved as Credit,
    available: (available ?? fmt(units(total) - units(reserved))) as Credit,
    signupGrantedAt: "2026-09-20T12:00:00.000000Z",
  };
}

const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const down = <T>(): Result<T> => ({
  ok: false,
  error: { code: "dependency_unavailable", message: "Account data is unavailable right now." },
});

const T = {
  figures: "U1R-B01 available, reserved and spent are exact credits from their own fields, never dollars",
  identity: "U1R-B02 available = balance - reserved, and a wallet that disagrees is flagged",
  states: "U1R-B03 no wallet, zero/negative, low and funded funds are four different states",
  failed: "U1R-B04 a failed wallet read is an error, never a zero, and a failed spent read is 'unavailable'",
  grant: "U1R-B05 the grant is one-time 10,000 credits, with no refill, expiry or payment offered",
  ledger: "U1R-B06 every ledger kind renders signed in credits, a debit links to its request, the platform is not a person",
  legacy: "U1R-B07 legacy USD is a separate USD section when history exists, and an error is not an empty history",
  page: "U1R-B08 the page model walks the ledger on its own cursors and states every branch",
};

test(T.figures, () => {
  const state = creditCardState(ok(wallet("9987.65432100", "12.00000001")), ok("10000.00000000" as Credit));
  assert.equal(state.kind, "ready");
  if (state.kind !== "ready") return;
  const byLabel = Object.fromEntries(state.value.figures.map((f) => [f.label, f.value]));
  assert.deepEqual(byLabel, {
    Available: "9,975.65432099 credits",
    Reserved: "12.00000001 credits",
    Spent: "12.345679 credits",
    Balance: "9,987.654321 credits",
  });
  assert.equal(state.value.figures[0].label, "Available");
  assert.ok(state.value.figures[0].emphasis);
  for (const figure of state.value.figures) {
    assert.doesNotMatch(`${figure.value} ${figure.hint}`, /\$|USD|dollar/i, figure.label);
  }
});

test(T.identity, () => {
  const good = creditCardState(ok(wallet("100.00000000", "0.00000001")), ok("100.00000000" as Credit));
  const bad = creditCardState(ok(wallet("100.00000000", "0.00000001", "100.00000000")), ok("100.00000000" as Credit));
  assert.ok(good.kind === "ready" && good.value.reconciles);
  assert.ok(bad.kind === "ready" && !bad.value.reconciles);
});

test(T.states, () => {
  assert.equal(creditAccountState(null).kind, "no_wallet");
  assert.equal(creditAccountState(wallet("10.00000000", "10.00000000")).kind, "exhausted");
  assert.equal(creditAccountState(wallet("0.00000000", "0.00000000")).kind, "exhausted");
  assert.equal(creditAccountState(wallet("5.00000000", "6.00000000")).kind, "exhausted");
  const below = (BigInt(LOW_FUNDS.replace(".", "")) - BigInt(1)).toString();
  const lowTotal = `${below.slice(0, -8)}.${below.slice(-8)}`;
  assert.equal(creditAccountState(wallet(lowTotal, "0.00000000")).kind, "low");
  assert.equal(creditAccountState(wallet(LOW_FUNDS, "0.00000000")).kind, "funded");
  const exhausted = creditAccountState(wallet("1.00000000", "1.00000000"));
  assert.ok(exhausted.kind === "exhausted");
  assert.match(exhausted.guidance, /402/);
  assert.match(exhausted.guidance, /insufficient_credit/);
  const none = creditAccountState(null);
  assert.ok(none.kind === "no_wallet");
  assert.match(none.guidance, /verif/i);
  // A new account with no wallet has no figures at all — not three zeroes.
  const card = creditCardState(ok(null), null);
  assert.ok(card.kind === "ready" && card.value.figures.length === 0 && card.value.state.kind === "no_wallet");
});

test(T.failed, () => {
  const failed = creditCardState(down(), null);
  assert.equal(failed.kind, "error");
  if (failed.kind === "error") assert.equal(failed.recovery, "retry");
  const partial = creditCardState(ok(wallet("10.00000000", "1.00000000")), down());
  assert.ok(partial.kind === "ready");
  if (partial.kind !== "ready") return;
  const spent = partial.value.figures.find((f) => f.label === "Spent");
  assert.equal(spent?.value, "Unavailable");
  const unbounded = creditCardState(ok(wallet("10.00000000", "1.00000000")), ok(null));
  assert.ok(unbounded.kind === "ready");
  if (unbounded.kind === "ready") {
    assert.equal(unbounded.value.figures.find((f) => f.label === "Spent")?.value, "Unavailable");
  }
});

test(T.grant, () => {
  const card = creditCardState(ok(wallet("10000.00000000", "0.00000000")), ok("10000.00000000" as Credit));
  assert.ok(card.kind === "ready");
  if (card.kind !== "ready") return;
  assert.match(card.value.grant, /one-time/i);
  assert.match(card.value.grant, /10,000/);
  assert.match(card.value.grant, /2026-09-20 12:00 UTC/);
  const everything = [CREDITS_NOTICE, card.value.grant, ...Object.values(creditAccountState(null)),
    ...Object.values(creditAccountState(wallet("0.00000000", "0.00000000")))].join(" ");
  assert.doesNotMatch(everything, /add credits|add card|top.?up|\bbuy\b|purchase|invoice|checkout|pay now|monthly|renews|requests? included/i);
  assert.match(CREDITS_NOTICE, /not refilled/);
  assert.match(CREDITS_NOTICE, /does not expire/);
  const ungranted = creditCardState(ok({ ...wallet("0.00000000", "0.00000000"), signupGrantedAt: null }), ok("0.00000000" as Credit));
  assert.ok(ungranted.kind === "ready" && /not received/i.test(ungranted.value.grant));
});

test(T.ledger, () => {
  const entry = (over: Partial<CreditLedgerEntry>): CreditLedgerEntry => ({
    id: "e1", createdAt: "2026-09-20T12:00:00.000000Z", kind: "signup_grant",
    amount: "10000.00000000" as Credit, requestId: null, reason: "", actor: "platform", ...over,
  });
  const grant = ledgerEntryView(entry({}));
  assert.deepEqual([grant.kind, grant.amount, grant.actor, grant.detailHref],
    ["One-time signup grant", "+10,000.00 credits", "infrx platform", null]);
  const debit = ledgerEntryView(entry({ kind: "inference_debit", amount: "-0.00012345" as Credit, requestId: "b1/../x", reason: "inference" }));
  assert.deepEqual([debit.kind, debit.amount, debit.detailHref],
    ["Request charge", "-0.00012345 credits", "/usage/b1%2F..%2Fx"]);
  assert.equal(ledgerEntryView(entry({ kind: "operator_adjustment", amount: "-5.00000000" as Credit, reason: "correction" })).kind, "Adjustment");
  assert.equal(ledgerEntryView(entry({ kind: "toString" })).kind, "Other");
  assert.equal(ledgerEntryView(entry({ reason: "" })).reason, "—");
});

test(T.legacy, () => {
  assert.equal(legacyUsdState(null).kind, "empty");
  assert.equal(legacyUsdState(ok<LegacyUsd>({ balance: "0.00000000" as never, entryCount: 0, rolloutHold: false })).kind, "empty");
  const held = legacyUsdState(ok<LegacyUsd>({ balance: "4.99980340" as never, entryCount: 3, rolloutHold: true }));
  assert.ok(held.kind === "ready");
  if (held.kind !== "ready") return;
  assert.equal(held.value.balance, "$4.9998034 USD");
  assert.match(held.value.text, /not credits/);
  assert.match(held.value.text, /not converted/);
  assert.ok(held.value.hold !== null);
  // A zero balance with history is still history (entries exist); no hold.
  const zero = legacyUsdState(ok<LegacyUsd>({ balance: "0.00000000" as never, entryCount: 2, rolloutHold: false }));
  assert.ok(zero.kind === "ready" && zero.value.hold === null && zero.value.balance === "$0.00 USD");
  assert.equal(legacyUsdState(down()).kind, "error");
});

test(T.page, () => {
  const e = (n: number): CreditLedgerEntry => ({
    id: `e${n}`, createdAt: "2026-09-20T12:00:00.000000Z", kind: "inference_debit",
    amount: "-1.00000000" as Credit, requestId: `r${n}`, reason: "inference", actor: "platform",
  });
  const page: Page<CreditLedgerEntry> = { items: [e(1), e(2)], next_cursor: "C2" };
  const first = creditsPageModel({
    state: { cursor: null, trail: [] },
    wallet: ok(wallet("98.00000000", "0.00000000")),
    creditsIn: ok("100.00000000" as Credit),
    ledger: ok(page),
    legacy: ok<LegacyUsd>({ balance: "0.00000000" as never, entryCount: 0, rolloutHold: false }),
  });
  assert.equal(first.here, "/billing");
  assert.ok(first.ledger.kind === "ready");
  if (first.ledger.kind !== "ready") return;
  assert.equal(first.ledger.value.nextHref, "/billing?cursor=C2");
  assert.equal(first.ledger.value.previousHref, null);
  assert.equal(first.ledger.value.page, 1);
  const second = creditsPageModel({
    state: { cursor: "C2", trail: [] },
    wallet: ok(wallet("98.00000000", "0.00000000")),
    creditsIn: ok("100.00000000" as Credit),
    ledger: ok({ items: [e(3)], next_cursor: null }),
    legacy: null,
  });
  assert.ok(second.ledger.kind === "ready" && second.ledger.value.previousHref === "/billing" && second.ledger.value.nextHref === null);
  // No wallet: no ledger was read, and that is "empty", not an error and not rows of zeroes.
  const fresh = creditsPageModel({ state: { cursor: null, trail: [] }, wallet: ok(null), creditsIn: null, ledger: null, legacy: null });
  assert.equal(fresh.ledger.kind, "empty");
  // A failed ledger read is an error with its recovery, never an empty ledger.
  const broken = creditsPageModel({ state: { cursor: null, trail: [] }, wallet: ok(wallet("1.00000000", "0.00000000")), creditsIn: ok("1.00000000" as Credit), ledger: down(), legacy: null });
  assert.ok(broken.ledger.kind === "error" && broken.ledger.recovery === "retry");
});
