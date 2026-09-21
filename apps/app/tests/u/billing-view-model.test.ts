// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// U1 promotional balance and ledger view model. The pilot has no payments (DEC-01), so the cases
// below hold two things at once: the arithmetic identity the contract promises, and the wording —
// a page that calls granted credit "revenue", or offers a card, is wrong even when its numbers add
// up.
import assert from "node:assert/strict";
import test from "node:test";

import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import { displayMoney, parseMoney, ZERO_MONEY } from "../../lib/contracts/money.ts";
import { PLATFORM_ACTOR, type LedgerEntry, type WalletBalance } from "../../lib/contracts/types.ts";
import {
  PROMOTIONAL_NOTICE,
  actorLabel,
  balanceFigures,
  balanceIsConsistent,
  balanceState,
  ledgerKindLabel,
  ledgerRowView,
  signedMoney,
} from "../../app/(console)/billing/view-model.ts";
import {
  firstCursorState,
  hasPreviousPage,
  ledgerHref,
  nextCursorState,
  pageNumberOf,
  parsePageCursor,
  previousCursorState,
  viewStateOf,
} from "../../app/(console)/usage/view-model.ts";

function services() {
  return createFakeConsoleServices();
}

function wallet(total: string, reserved: string, available: string): WalletBalance {
  return {
    ledger_total: parseMoney(total),
    reserved_total: parseMoney(reserved),
    available: parseMoney(available),
  };
}

test("U1-T20 the three figures are the three the wallet reports, and available leads", async () => {
  const fake = services();
  const balance = await fake.balances(fake.sessions.owner);
  assert.ok(balance.ok);
  assert.notEqual(
    balance.value.available,
    balance.value.ledger_total,
    "the fixture must hold outstanding holds, or this page proves nothing",
  );

  const figures = balanceFigures(balance.value);
  const byLabel = new Map(figures.map((figure) => [figure.label, figure]));
  assert.equal(figures.length, 3);
  assert.equal(byLabel.get("Available now")?.value, displayMoney(balance.value.available));
  assert.equal(byLabel.get("Promotional credit")?.value, displayMoney(balance.value.ledger_total));
  assert.equal(byLabel.get("Reserved")?.value, displayMoney(balance.value.reserved_total));
  assert.equal(byLabel.get("Available now")?.emphasis, true, "available is the number people act on");
  assert.equal(byLabel.get("Promotional credit")?.emphasis, false);
  assert.equal(new Set(figures.map((figure) => figure.value)).size, 3, "three distinct figures");
});

test("U1-T21 available = promotional credit - reserved, and a wallet that disagrees is flagged", async () => {
  const fake = services();
  const balance = await fake.balances(fake.sessions.owner);
  assert.ok(balance.ok);
  assert.equal(balanceIsConsistent(balance.value), true, "the contract's own identity");

  assert.equal(balanceIsConsistent(wallet("10.00000000", "2.50000000", "7.50000000")), true);
  assert.equal(
    balanceIsConsistent(wallet("10.00000000", "2.50000000", "7.50000001")),
    false,
    "one unit of 1e-8 USD out is still out",
  );
  assert.equal(balanceIsConsistent(wallet("10.00000000", "12.00000000", "-2.00000000")), true);
});

test("U1-T22 a new organization, an exhausted one and a funded one get different guidance", async () => {
  const fake = services();

  const fresh = await fake.balances(fake.sessions.otherOwner);
  assert.ok(fresh.ok);
  assert.equal(fresh.value.ledger_total, ZERO_MONEY, "the fixture's new organization has no grants");
  const ledger = await fake.ledger(fake.sessions.otherOwner, { limit: 1 });
  assert.ok(ledger.ok);
  assert.equal(ledger.value.items.length, 0, "and no ledger history");

  const isNew = balanceState(fresh.value, ledger.value.items.length > 0);
  assert.equal(isNew.kind, "new");
  assert.ok(isNew.kind === "new");
  assert.match(isNew.guidance, /operator/i, "a new organization is told where credit comes from");
  assert.match(isNew.guidance, /no card|nothing to buy/i, "and that there is nothing to buy");

  // Spent or fully reserved: a history, a zero, and a different next step.
  const exhausted = balanceState(wallet("30.00000000", "30.00000000", "0.00000000"), true);
  assert.equal(exhausted.kind, "exhausted");
  const overdrawn = balanceState(wallet("30.00000000", "31.00000000", "-1.00000000"), true);
  assert.equal(overdrawn.kind, "exhausted");

  const funded = await fake.balances(fake.sessions.owner);
  assert.ok(funded.ok);
  assert.equal(balanceState(funded.value, true).kind, "funded");

  // A zero ledger total with history is not a brand-new organization.
  assert.equal(balanceState(wallet("0.00000000", "0.00000000", "0.00000000"), true).kind, "exhausted");
});

test("U1-T23 no customer-facing balance wording offers payment or calls the credit revenue", () => {
  // The guidance is excluded from the scan on purpose: it *denies* payment ("no card to add,
  // nothing to buy"), so a word-level scan would read the denial as an offer. It is asserted
  // positively in U1-T22. The absence of a payment control itself is markup, and E's e2e suite.
  const strings = [
    PROMOTIONAL_NOTICE,
    ...balanceFigures(wallet("1.00000000", "0.00000000", "1.00000000")).flatMap((figure) => [
      figure.label,
      figure.hint,
    ]),
    ...(["new", "exhausted"] as const).map((kind) => {
      const state = kind === "new"
        ? balanceState(wallet("0.00000000", "0.00000000", "0.00000000"), false)
        : balanceState(wallet("1.00000000", "1.00000000", "0.00000000"), true);
      return state.kind === "funded" ? "" : state.headline;
    }),
  ];
  assert.match(PROMOTIONAL_NOTICE, /promotional/i);
  assert.match(PROMOTIONAL_NOTICE, /not a cash balance/i);
  for (const value of strings) {
    assert.doesNotMatch(
      value,
      /add credits|add card|top ?up|buy|purchase|invoice|checkout|revenue|pay now/i,
      `payment or revenue wording in customer copy: ${value}`,
    );
  }
});

test("U1-T24 a credit reads as a credit and a debit keeps its sign", () => {
  assert.equal(signedMoney(parseMoney("25.00000000")), "+$25.00");
  assert.equal(signedMoney(parseMoney("-0.00012345")), "-$0.00012345");
  assert.equal(signedMoney(ZERO_MONEY), "$0.00", "zero is neither");
  assert.equal(signedMoney(parseMoney("0.00000001")), "+$0.00000001", "the eighth digit survives");
});

test("U1-T25 every ledger kind renders, and an operator's entry names the platform, not a person", async () => {
  const fake = services();
  const page = await fake.ledger(fake.sessions.owner, { limit: 100 });
  assert.ok(page.ok);

  const byKind = new Map<string, LedgerEntry>();
  let cursor: string | null = page.value.next_cursor;
  for (const entry of page.value.items) if (!byKind.has(entry.kind)) byKind.set(entry.kind, entry);
  for (let guard = 0; guard < 10 && cursor !== null; guard += 1) {
    const more = await fake.ledger(fake.sessions.owner, { limit: 100, cursor });
    assert.ok(more.ok);
    for (const entry of more.value.items) if (!byKind.has(entry.kind)) byKind.set(entry.kind, entry);
    cursor = more.value.next_cursor;
  }
  for (const kind of ["grant", "usage", "adjustment", "purchase"] as const) {
    assert.ok(byKind.has(kind), `the fixture must cover the ${kind} kind (R13 keeps purchase readable)`);
    assert.ok(ledgerKindLabel(kind).length > 0);
  }
  assert.equal(new Set(["grant", "usage", "adjustment", "purchase"].map(
    (kind) => ledgerKindLabel(kind as LedgerEntry["kind"]),
  )).size, 4, "four kinds, four labels");

  const grant = byKind.get("grant")!;
  assert.equal(grant.actor, PLATFORM_ACTOR, "R41: a customer session never sees the operator");
  const grantView = ledgerRowView(grant);
  assert.equal(grantView.kind, "Promotional grant");
  assert.equal(grantView.actor, "infrx platform");
  assert.doesNotMatch(grantView.actor, /@/, "no principal, and nothing that looks like one");
  assert.equal(grantView.reason, grant.reason, "the grant reason is customer-visible text, verbatim");
  assert.ok((grant.reason ?? "").length > 0);
  assert.equal(grantView.amount, signedMoney(grant.delta));
  assert.equal(grantView.credit, true);

  const debit = byKind.get("usage")!;
  const debitView = ledgerRowView(debit);
  assert.equal(debitView.actor, "—", "nobody made a usage debit happen");
  assert.equal(debitView.reason, "—", "and it has no reason to show");
  assert.equal(debitView.credit, false);
  assert.match(debitView.amount, /^-\$/);

  assert.equal(actorLabel(null), "—");
  assert.equal(actorLabel(PLATFORM_ACTOR), "infrx platform");
  assert.equal(actorLabel("member@northwind.example"), "member@northwind.example");
  assert.equal(ledgerRowView(byKind.get("purchase")!).kind, "Purchase (legacy)");
});

test("U1-T26 the ledger walks forward and back on its own cursors", async () => {
  const fake = services();
  const forward: { state: { cursor: string | null; trail: readonly string[] }; ids: string[] }[] = [];
  let state: { cursor: string | null; trail: readonly string[] } = { cursor: null, trail: [] };
  for (;;) {
    const result = await fake.ledger(fake.sessions.owner, {
      limit: 25,
      ...(state.cursor === null ? {} : { cursor: state.cursor }),
    });
    assert.ok(result.ok);
    forward.push({ state, ids: result.value.items.map((entry) => entry.id) });
    assert.equal(pageNumberOf(state), forward.length);
    if (result.value.next_cursor === null) break;
    assert.ok(forward.length < 20, "the ledger walk must terminate");
    state = nextCursorState(state, result.value.next_cursor);
  }
  assert.ok(forward.length >= 4, `expected several ledger pages, walked ${forward.length}`);
  const ids = forward.flatMap((entry) => entry.ids);
  assert.equal(new Set(ids).size, ids.length, "no entry appears twice");

  let back = forward[forward.length - 1].state;
  for (let index = forward.length - 1; index >= 0; index -= 1) {
    assert.deepEqual(back, forward[index].state, `ledger page ${index + 1} must be reachable backwards`);
    assert.equal(hasPreviousPage(back), index > 0);
    back = previousCursorState(back);
  }
  assert.deepEqual(firstCursorState(forward[2].state), forward[0].state);

  const href = ledgerHref(forward[2].state);
  assert.deepEqual(
    parsePageCursor(Object.fromEntries(groupSearch(href))),
    { cursor: forward[2].state.cursor, trail: [...forward[2].state.trail] },
    "the link a Next/Previous control produces parses back to the same state",
  );

  const failing = services();
  failing.failNext("ledger", "dependency_unavailable");
  const broken = await failing.ledger(failing.sessions.owner, { limit: 25 });
  const view = viewStateOf(broken, (value: { items: unknown[] }) => value.items.length === 0);
  assert.equal(view.kind, "error");
  assert.ok(view.kind === "error" && view.recovery === "retry");
});

function groupSearch(href: string): [string, string | string[]][] {
  const grouped = new Map<string, string[]>();
  for (const [name, value] of new URLSearchParams(href.slice(href.indexOf("?") + 1))) {
    grouped.set(name, [...(grouped.get(name) ?? []), value]);
  }
  return [...grouped].map(([name, values]) => [name, values.length === 1 ? values[0] : values]);
}
