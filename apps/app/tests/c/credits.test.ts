// node --test "tests/**/*.test.ts"
//
// The console sidebar's available balance (C1 acceptance: "migrate credits to wallet/hold views and
// test current selected-org behaviour without adding org switching"; S1-fix B1).
//
// `lib/credits.ts` itself reaches Supabase through `next/headers`, so it cannot be loaded here (R48).
// The decisions are in `lib/services/credits.ts`: what a failing read means, what reaches the sidebar,
// and arithmetic that never touches `Number`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { displayMoney, parseMoney, ZERO_MONEY } from "../../lib/contracts/money.ts";
import { balanceOutcome, sidebarBalance, type BalanceResult } from "../../lib/services/credits.ts";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const source = (relative: string) => readFileSync(join(appRoot, relative), "utf8");

/**
 * The same file with its comments removed, so a rule about the *code* is not satisfied or broken by
 * prose: "never throws" in a doc comment is not a `throw`, and a `throw` is not excused by a comment
 * around it. Coarse on purpose — none of the three files checked here puts `//` inside a string.
 */
const code = (relative: string) =>
  source(relative)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "");

/**
 * The outcome, with "it threw" as a fourth kind rather than as a test crash.
 *
 * `assert.throws`-style checking is the wrong shape here: the invariant is that nothing is thrown, and
 * a case that fell over with the exception would be reported as an error rather than as a failed
 * assertion — which the mutation runner classifies as a broken copy, not as a kill (R32/R40).
 */
function outcomeOf(data: Parameters<typeof balanceOutcome>[0], error: Parameters<typeof balanceOutcome>[1]) {
  try {
    return balanceOutcome(data, error);
  } catch (thrown) {
    return { kind: "threw" as const, reason: String(thrown) };
  }
}

const wallet = (ledger_total: number | string | null, reserved_total: number | string | null) => ({
  ledger_total,
  reserved_total,
});

test("available is the reconciled total minus reservations, on scaled integers", () => {
  // 10.00000003 - 0.00000004 in floats is 9.99999999000000x; the eighth digit is the whole point.
  assert.deepEqual(outcomeOf(wallet("10.00000003", "0.00000004"), null), { kind: "ok", money: "9.99999999" });

  // Past 2^53 scaled units a float cannot hold the eighth digit at all: this subtraction comes back
  // as 123456789012.00000000 in `Number`, which is a cent the customer does not have.
  assert.deepEqual(outcomeOf(wallet("123456789012.00000001", "0.00000002"), null), {
    kind: "ok",
    money: "123456789011.99999999",
  });

  // A hold lowers what is available, and a wallet with nothing in it is zero rather than unavailable.
  assert.deepEqual(outcomeOf(wallet("5.00000000", "1.25000000"), null), { kind: "ok", money: "3.75000000" });
  assert.deepEqual(outcomeOf(wallet(null, null), null), { kind: "ok", money: ZERO_MONEY });
});

test("a missing wallet function is unavailable, never the hold-blind legacy balance", () => {
  // Pre-D1 `org_wallet_summary` may not exist. Answering from `org_balance` instead would report the
  // ledger total — every outstanding hold ignored — as available: an inflated number a customer acts
  // on. 0003-0005 ship the function, so its absence is a deployment defect to surface.
  for (const error of [
    { code: "PGRST202", message: "Could not find the function public.org_wallet_summary(p_org) in the schema cache" },
    { code: "42883", message: "function public.org_wallet_summary(uuid) does not exist" },
    { code: "42501", message: "permission denied for function org_wallet_summary" },
    { code: "57014", message: "canceling statement due to statement timeout" },
    { code: "PGRST301", message: "JWT expired" },
    { code: null, message: "Could not find the function in the schema cache" },
  ]) {
    const outcome = outcomeOf(wallet("99.00000000", "9.00000000"), error);
    assert.equal(
      outcome.kind,
      "unavailable",
      `${String(error.code)}: a failed wallet read is returned as unavailable, not answered and not raised`,
    );
    assert.equal(sidebarBalance(outcome as BalanceResult), null, `${String(error.code)}: and no amount is shown`);
  }
});

test("a wallet row that is absent or inexact is unavailable, not a zero balance", () => {
  // A deployed summary function returning no row must not be shown as an empty wallet: "0.00" is an
  // amount, and a customer with credit would act on being told they have none.
  for (const outcome of [outcomeOf(null, null), outcomeOf(undefined, null)]) {
    assert.equal(outcome.kind, "unavailable", "a missing wallet row is unavailable");
    assert.equal(sidebarBalance(outcome as BalanceResult), null, "and the sidebar shows no amount");
  }
  // Nor is a wallet whose amount cannot be read exactly: see the bound below.
  const inexact = outcomeOf(wallet(123456789012.12345678, "0"), null);
  assert.equal(inexact.kind, "unavailable", "an amount whose digits would be invented is unavailable");
  assert.equal(sidebarBalance(inexact as BalanceResult), null);
});

test("the sidebar is handed an amount only when the wallet answered", () => {
  assert.equal(sidebarBalance({ kind: "ok", money: parseMoney("3.75000000") }), "$3.75");
  assert.equal(sidebarBalance({ kind: "ok", money: ZERO_MONEY }), displayMoney(ZERO_MONEY));
  assert.equal(
    sidebarBalance({ kind: "unavailable", reason: "the wallet summary returned no wallet row" }),
    null,
    "unavailable is not rendered as a number: the sidebar shows fixed copy instead",
  );
});

test("a wallet amount that arrives as a number is bounded by what a double can hold", () => {
  // `toFixed(8)` does not convert above ~2^26, it fabricates: 123456789012.12345678 comes back as
  // …12345886. Such a row is refused (and so unavailable) rather than rounded into the balance.
  for (const value of [123456789012.12345678, 2 ** 26, -123456789012.12345678, -(2 ** 26), Number.POSITIVE_INFINITY, NaN]) {
    // The bound is on the magnitude: a negative amount fabricates its last digits exactly as a
    // positive one does, and `reserved_total` decides the balance just as `ledger_total` does.
    assert.equal(outcomeOf(wallet(value, "0"), null).kind, "unavailable", `ledger_total ${value}`);
    assert.equal(outcomeOf(wallet("0", value), null).kind, "unavailable", `reserved_total ${value}`);
  }
  // And just inside the bound, on both signs, a number is still exact.
  assert.deepEqual(outcomeOf(wallet(2 ** 26 - 1, 0.5), null), { kind: "ok", money: "67108862.50000000" });
  assert.deepEqual(outcomeOf(wallet(1.25, -0.25), null), { kind: "ok", money: "1.50000000" });
});

/**
 * The rendering half of B1. A DOM render is not available: `node --test` cannot load a `.tsx` module
 * at all (ERR_UNKNOWN_FILE_EXTENSION — it strips types but does not transform JSX), and the console's
 * dependency set is frozen, so this checks the three files as source, the way `client-boundary.test.ts`
 * checks the import graph. It catches the regression shapes: a number formatted or invented in the
 * composition root, an unavailable balance turned back into a 500, a gate short-circuited in the
 * sidebar, and copy that states an amount or the reason a read failed.
 */
test("the layout hands the sidebar a result it cannot turn into a number, and the copy states none", () => {
  const layout = code("app/(console)/layout.tsx");
  // Legacy (USD org summary) until WR-1 lands, then the consumer's CREDIT wallet (C0), then (app-union)
  // U1R's wallet figure behind C0's shell; either way the wallet's own result goes through the sidebar
  // mapper, and nothing else.
  assert.match(
    layout,
    /const balance = (sidebarBalance\(await getBalance\(session\.orgId\)\)|shell\.reads === null \? null : sidebarCredit\(await shell\.reads\.balance\(\)\)|shell\.reads === null \? null : sidebarCredits\(await \(await consumerCreditReads\(\)\)\.reads\.wallet\(\)\));/,
    "the layout maps the wallet's own result through sidebarBalance/sidebarCredit, and nothing else",
  );
  assert.match(layout, /balance=\{balance\}/, "and hands exactly that to the sidebar");
  assert.doesNotMatch(
    layout,
    /displayMoney|ZERO_MONEY|toFixed/,
    "the layout must format no money of its own: an amount here is one the wallet never produced",
  );
  assert.doesNotMatch(
    layout,
    /\?\?|"\$|'\$/,
    "and must not substitute a default amount for a missing one: `?? \"$0.00\"` is a balance nobody read",
  );
  assert.doesNotMatch(
    layout,
    /\bthrow\b/,
    "an unavailable balance must not become a thrown error: there is no error.tsx above this layout, " +
      "so every console page would 500",
  );

  const sidebar = code("components/sidebar.tsx");
  assert.match(sidebar, /BALANCE_UNAVAILABLE/, "the sidebar renders the shared unavailable copy");
  assert.match(
    sidebar,
    /balance === null \?/,
    "on the balance being absent — a constant condition here would render the amount branch with no amount",
  );
  assert.doesNotMatch(sidebar, /ZERO_MONEY|displayMoney/, "and never substitutes an amount for it");

  const copy = source("components/console-data-state.tsx");
  const literal = /BALANCE_UNAVAILABLE = "([^"]*)"/.exec(copy);
  assert.ok(literal, "the fixed copy is a literal in console-data-state.tsx");
  assert.doesNotMatch(literal[1], /\d/, "the unavailable copy states no amount, not even a zero");
});

/**
 * `lib/credits.ts` is the one place the decisions above can be undone, and it is the one place no case
 * can execute: it imports `@/lib/supabase/server`, so loading it here means `next/headers` (R48). It
 * is deliberately a shell — one wallet call, one pure decision, one guard — and this reads that shape
 * out of the source, so a second read, a fallback under another function's name, or a `throw` cannot
 * come back unnoticed between here and C0's real context.
 */
test("the balance read is one wallet call that cannot throw", () => {
  const credits = code("lib/credits.ts");

  const calls = [...credits.matchAll(/\.rpc\(\s*"([^"]*)"/g)].map((match) => match[1]);
  assert.deepEqual(
    calls,
    ["org_wallet_summary"],
    "exactly one RPC, and it is the wallet summary: a second call is the hold-blind `org_balance` " +
      "fallback returning under whatever name, and it reports credit that is already committed",
  );
  assert.doesNotMatch(
    credits,
    /\.from\(|\.rpc\(\s*[^"]/,
    "and no table read and no computed function name: the balance has one source",
  );

  assert.match(credits, /\btry\s*\{/, "the read is guarded");
  assert.match(credits, /\}\s*catch\b/, "and the guard catches: a client or transport that throws is " +
    "unavailable, not a 500 on every console page");
  assert.doesNotMatch(
    credits,
    /\bthrow\b/,
    "and nothing in this module throws: the console layout calls it on every page with no error.tsx " +
      "above it",
  );
  assert.match(
    credits,
    /return balanceOutcome\(summary\.data, summary\.error\)/,
    "the decision itself stays in lib/services/credits.ts, where the cases above can reach it",
  );
});
