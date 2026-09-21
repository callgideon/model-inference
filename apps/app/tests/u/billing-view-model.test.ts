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
import {
  PLATFORM_ACTOR,
  type ErrorCode,
  type LedgerEntry,
  type Page,
  type Result,
  type WalletBalance,
} from "../../lib/contracts/types.ts";
import {
  LEDGER_PAGE_SIZE,
  PROMOTIONAL_NOTICE,
  UNKNOWN_KIND_LABEL,
  actorLabel,
  balanceCardModel,
  balanceCardState,
  balanceFigures,
  balanceIsConsistent,
  balanceState,
  billingPageModel,
  hasLedgerHistory,
  historyProbeQuery,
  ledgerKindLabel,
  ledgerPageQuery,
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

  // Both halves of the new-organization test have to matter. A funded wallet whose history we could
  // not read is *not* new — and this is reachable: the ledger read beside the balance can fail on its
  // own, and greeting a funded organization with "ask for your first grant" is the failure mode.
  assert.equal(
    balanceState(funded.value, false).kind,
    "funded",
    "money in the wallet means the organization is not new, whatever the history says",
  );
  assert.equal(balanceState(wallet("30.00000000", "30.00000000", "0.00000000"), false).kind, "exhausted");

  // The exhausted guidance still has to say where credit comes from; it is the same dead end.
  const exhaustedState = balanceState(wallet("30.00000000", "30.00000000", "0.00000000"), true);
  assert.ok(exhaustedState.kind === "exhausted");
  assert.match(exhaustedState.guidance, /operator/i);
  assert.match(exhaustedState.guidance, /spent or reserved/i);
});

test("U1-T27 a failed ledger read never makes an established organization look new", async () => {
  const fake = services();
  const funded = await fake.balances(fake.sessions.owner);
  assert.ok(funded.ok);

  const empty: Result<Page<LedgerEntry>> = { ok: true, value: { items: [], next_cursor: null } };
  const failed: Result<Page<LedgerEntry>> = {
    ok: false,
    error: { code: "dependency_unavailable" as ErrorCode, message: "injected failure" },
  };

  assert.equal(hasLedgerHistory(empty), false, "an empty ledger really is no history");
  assert.equal(
    hasLedgerHistory(failed),
    true,
    "but a failed read is not evidence of absence — it must not read as 'no history'",
  );
  assert.equal(
    hasLedgerHistory(empty, { cursor: "c1", trail: [] }),
    true,
    "and being on a later page is history whatever this page holds",
  );

  const fresh = await fake.balances(fake.sessions.otherOwner);
  assert.ok(fresh.ok);
  assert.equal(balanceCardModel(fresh.value, hasLedgerHistory(empty)).state.kind, "new");
  assert.equal(
    balanceCardModel(fresh.value, hasLedgerHistory(failed)).state.kind,
    "exhausted",
    "a zero wallet with an unreadable ledger gets the neutral dead-end, not the welcome",
  );

  // The card is a state: a failed `balances` read is an error, not a card that quietly disappears.
  const broken = services();
  broken.failNext("balances", "dependency_unavailable");
  const state = balanceCardState(await broken.balances(broken.sessions.owner), empty);
  assert.equal(state.kind, "error");
  assert.ok(state.kind === "error" && state.recovery === "retry");

  const ready = balanceCardState(funded, empty);
  assert.ok(ready.kind === "ready");
  assert.equal(ready.value.figures.length, 3);
  assert.equal(ready.value.reconciles, true);
  assert.equal(ready.value.notice, PROMOTIONAL_NOTICE);
  assert.equal(
    balanceCardModel({ ...funded.value, available: parseMoney("1.00000000") }, true).reconciles,
    false,
    "and a wallet that does not reconcile says so through the model, not by looking right",
  );
});

test("U1-T28 the billing page model states every branch, and page sizes are named here", async () => {
  const fake = services();
  const state = { cursor: null, trail: [] };
  assert.deepEqual(ledgerPageQuery(state), { limit: LEDGER_PAGE_SIZE });
  assert.deepEqual(ledgerPageQuery({ cursor: "c1", trail: [] }), {
    limit: LEDGER_PAGE_SIZE,
    cursor: "c1",
  });
  assert.deepEqual(historyProbeQuery(), { limit: 1 }, "the usage page only asks whether any row exists");

  const balance = await fake.balances(fake.sessions.owner);
  const ledger = await fake.ledger(fake.sessions.owner, ledgerPageQuery(state));
  const model = billingPageModel({ state, balance, ledger });
  assert.equal(model.balance.kind, "ready");
  assert.ok(model.ledger.kind === "ready");
  assert.equal(model.ledger.value.rows.length, LEDGER_PAGE_SIZE);
  assert.equal(model.ledger.value.page, 1);
  assert.equal(model.ledger.value.previousHref, null);
  assert.ok(model.ledger.value.nextHref !== null);
  assert.equal(model.here, "/billing");

  const failedLedger: Result<Page<LedgerEntry>> = {
    ok: false,
    error: { code: "dependency_unavailable" as ErrorCode, message: "injected failure" },
  };
  const brokenLedger = billingPageModel({ state, balance, ledger: failedLedger });
  assert.equal(brokenLedger.ledger.kind, "error", "a failed ledger read is an error, not an empty table");
  assert.equal(brokenLedger.balance.kind, "ready", "and the balance card survives it");

  const emptyLedger: Result<Page<LedgerEntry>> = { ok: true, value: { items: [], next_cursor: null } };
  assert.equal(billingPageModel({ state, balance, ledger: emptyLedger }).ledger.kind, "empty");

  // Page two and beyond: Previous exists, and the retry target carries the cursor of the page being
  // shown rather than sending the reader back to the start.
  assert.ok(ledger.ok && ledger.value.next_cursor !== null);
  const second = nextCursorState(state, ledger.value.next_cursor);
  const secondLedger = await fake.ledger(fake.sessions.owner, ledgerPageQuery(second));
  const secondModel = billingPageModel({ state: second, balance, ledger: secondLedger });
  assert.ok(secondModel.ledger.kind === "ready");
  assert.equal(secondModel.ledger.value.page, 2);
  assert.ok(secondModel.ledger.value.previousHref !== null, "page 2 can go back");
  assert.equal(secondModel.ledger.value.firstHref, "/billing");
  assert.ok(secondModel.here.includes("cursor="), "and the retry target keeps the cursor");

  // Walk to the last page: no Next there.
  let last = state;
  for (let guard = 0; guard < 20; guard += 1) {
    const page = await fake.ledger(fake.sessions.owner, ledgerPageQuery(last));
    assert.ok(page.ok);
    if (page.value.next_cursor === null) break;
    last = nextCursorState(last, page.value.next_cursor);
  }
  const lastModel = billingPageModel({ state: last, balance, ledger: await fake.ledger(fake.sessions.owner, ledgerPageQuery(last)) });
  assert.ok(lastModel.ledger.kind === "ready");
  assert.equal(lastModel.ledger.value.nextHref, null, "the last ledger page offers no Next");

  // A later page whose items happen to be empty is still not a new organization: the cursor says
  // there is history behind it. This has to be asserted on the ZERO wallet — the funded one is never
  // "new" whatever the history says, so asserting it there passes however the state is computed and
  // says nothing about whether the page cursor was consulted at all.
  const zeroWallet = await fake.balances(fake.sessions.otherOwner);
  assert.ok(zeroWallet.ok && zeroWallet.value.ledger_total === ZERO_MONEY, "a wallet that can be new");

  const firstPage = balanceCardState(zeroWallet, emptyLedger, { cursor: null, trail: [] });
  assert.ok(firstPage.kind === "ready");
  assert.equal(
    firstPage.value.state.kind,
    "new",
    "an empty first page of a zero wallet is a new organization",
  );

  const onLaterPage = balanceCardState(zeroWallet, emptyLedger, { cursor: "c1", trail: [] });
  assert.ok(onLaterPage.kind === "ready");
  assert.equal(
    onLaterPage.value.state.kind,
    "exhausted",
    "but on a later page the cursor is history, so the same wallet is not new",
  );
  assert.notEqual(onLaterPage.value.state.kind, firstPage.value.state.kind, "the cursor decides");
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
      /add credits|add card|top ?up|\bbuy\b|purchase|invoice|checkout|revenue|pay now|credit card/i,
      `payment or revenue wording in customer copy: ${value}`,
    );
  }

  // The ledger kind labels go through the same scan, with exactly one allowed exception: the legacy
  // `purchase` row has to render as what it is (R13). Anything else matching is a new offer to pay.
  const PURCHASE_LABEL = "Purchase (legacy)";
  assert.equal(ledgerKindLabel("purchase"), PURCHASE_LABEL, "the one allowed exception, named");
  for (const kind of ["grant", "usage", "adjustment", "purchase"] as const) {
    const label = ledgerKindLabel(kind);
    if (label === PURCHASE_LABEL) continue;
    assert.doesNotMatch(
      label,
      /add credits|add card|top ?up|\bbuy\b|purchase|invoice|checkout|revenue|pay now|credit card/i,
      `payment wording in the ${kind} label: ${label}`,
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

  // The kind comes from the service, so the label table is a boundary like any other. A bare
  // `KIND_LABELS[kind]` answered `toString` with a *function* and an unrecognised kind with
  // `undefined`, and both reached the table as a React child.
  for (const key of ["toString", "__proto__", "constructor", "hasOwnProperty", "valueOf"]) {
    const label = ledgerKindLabel(key as LedgerEntry["kind"]);
    assert.equal(typeof label, "string", `${key} must not yield a non-string label`);
    assert.equal(label, UNKNOWN_KIND_LABEL, `${key} is not a ledger kind`);
  }
  // Compared against the constant AND its value: `UNKNOWN_KIND_LABEL = ""` is exactly the blank cell
  // the guard exists to prevent, and a constant-only comparison accepts it.
  assert.equal(UNKNOWN_KIND_LABEL, "Other");
  assert.equal(ledgerKindLabel("refund" as LedgerEntry["kind"]), UNKNOWN_KIND_LABEL);
  const strange = ledgerRowView({ ...grant, kind: "constructor" as LedgerEntry["kind"] });
  assert.equal(strange.kind, UNKNOWN_KIND_LABEL, "and a row carrying one still renders");

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
