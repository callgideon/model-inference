// node --test "tests/**/*.test.ts"
//
// U3: minimal operator controls (`app/(console)/admin/`). The operator surface is a protected
// operations view, not the provider Lab. What these cases pin:
//
// - there is no arbitrary balance edit any more: the legacy `addCredit` (a free-form USD row
//   written with the service key) is gone, and nothing in admin/ holds a service key;
// - the page answers 404 to anyone without operator authority BEFORE it reads anything;
// - the operator port sends exactly the audited operation to infrx-api (`POST /operator/v1/*`, the
//   reason in the body and the form's key as Idempotency-Key; who acts is decided by the API from
//   the operator's own session, never by a field), and maps every refusal to a typed code with fixed
//   text - an API message never reaches the page, and an answer it cannot read is "not confirmed";
// - the reads (`GET /operator/v1/*`) are exact CREDIT decimal strings or an explicit unavailable
//   state per section, never a zero, a number or a fixture;
// - a form keeps its idempotency key across every failure (a retry cannot apply twice) and rotates
//   it only after a committed change.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import type { OperatorCommand } from "../../lib/services/actions.ts";
import type { Credit } from "../../lib/contracts/v2/money-units.ts";
import { answer, envelope, recordingApi, type Sent } from "../../lib/api/fake.ts";
import { apiOperatorPort } from "../../app/(console)/admin/operator-port.ts";
import { ACCOUNT_LIMIT, AUDIT_LIMIT, DRIFT_LIMIT, UNKNOWN_LIMIT, operatorReads } from "../../app/(console)/admin/operator-reads.ts";
import { OPERATOR_FORMS, formInput, nextKey, outcomeOf } from "../../app/(console)/admin/operator-form.ts";

const ADMIN = join(import.meta.dirname, "../../app/(console)/admin");
const USER = "c1000000-0000-4000-8000-000000000001";
const ORG = "0a000000-0000-4000-8000-0000000000aa";
const WALLET = "a1000000-0000-4000-8000-00000000000a";
const KEY_ID = "b1000000-0000-4000-8000-00000000000b";
const AUDITED = { reason: "support ticket 42", idempotency_key: "k-1", actor: "operator:someone" };

// ------------------------------------------------------------------------------------ surface

test("U3-S01 no arbitrary balance edit: admin/ writes no ledger row and holds no service key", () => {
  assert.equal(existsSync(join(ADMIN, "actions.ts")), false, "the legacy addCredit server action is deleted");
  for (const file of readdirSync(ADMIN)) {
    const source = readFileSync(join(ADMIN, file), "utf8");
    for (const banned of ["createAdminClient", "SUPABASE_SERVICE_ROLE_KEY", "credit_ledger", "delta_usd", ".insert(", ".update(", ".upsert(", ".delete("]) {
      assert.ok(!source.includes(banned), `${file} must not contain ${banned}`);
    }
  }
});

test("U3-S02 the page refuses a non-operator (404) before any read, and reads with the session's own client", () => {
  const page = readFileSync(join(ADMIN, "page.tsx"), "utf8");
  const gate = page.indexOf("if (!session.isOperator) notFound();");
  const read = page.indexOf("operatorReads(");
  assert.ok(gate > 0, "the operator gate is present");
  assert.ok(read > gate, "the reads run only after the gate");
  assert.match(page, /operatorReads\(\(await apiSource\(\)\)\.api\)/, "the reads use the request's API client (the operator's own session)");
  assert.ok(!/fixture/i.test(page), "no fixture reaches the operator page");
});

test("U3-S03 the forms call the shared audited server action and show no unsupported control", () => {
  const forms = readFileSync(join(ADMIN, "operator-forms.tsx"), "utf8");
  assert.match(forms, /^"use client";/);
  assert.match(forms, /import \{ operatorAction \} from "@\/app\/actions";/);
  assert.ok(!/grant_initial|publish|set_balance/.test(forms), "no control for an operation the console cannot perform");
  assert.deepEqual(
    OPERATOR_FORMS.map((form) => form.action),
    ["adjust_credit", "set_suspension", "revoke_key"],
  );
});

// The `.tsx` files cannot load under node --test, so their wiring is pinned as source (as S02 does).
test("U3-S04 the form submits the key it holds and keeps it through nextKey(); a fresh key is only the initial state or the rotation", () => {
  const forms = readFileSync(join(ADMIN, "operator-forms.tsx"), "utf8");
  const count = (pattern: RegExp) => forms.match(pattern)?.length ?? 0;
  // The submission carries the held key...
  assert.match(forms, /const input = formInput\(form\.action, [^;]+, key\);/, "the form submits the key held in state");
  assert.match(forms, /await operatorAction\(input\)/);
  // ...and the only key change is nextKey(): kept after any failure, rotated after a commit.
  assert.equal(count(/setKey\(/g), 1, "exactly one place changes the key");
  assert.match(forms, /setKey\(nextKey\(key, result, newKey\)\);/, "the key changes only through nextKey()");
  // newKey is defined once and used only as the initial state and nextKey's rotation argument.
  assert.equal(count(/\bnewKey\b/g), 3, "newKey: its definition, the initial state, the rotation");
  assert.match(forms, /const newKey = \(\) => crypto\.randomUUID\(\);/);
  assert.match(forms, /useState\(newKey\)/);
  assert.equal(count(/randomUUID/g), 1, "no other fresh key");
});

test("U3-S05 every page section goes through Section, and a failed read renders 'Unavailable', never its table", () => {
  const page = readFileSync(join(ADMIN, "page.tsx"), "utf8");
  const section = /\nfunction Section<T>\([^\n]*\{\n([\s\S]*?)\n\}\n/.exec(page)?.[1];
  assert.ok(section !== undefined, "the Section component is present");
  const failed = section.indexOf("if (!result.ok) {");
  const unavailable = section.indexOf("Unavailable: {result.error.message}.");
  const rendered = section.indexOf("children(");
  assert.ok(failed >= 0 && unavailable > failed, "a failed read returns the 'Unavailable' status");
  assert.ok(rendered > unavailable, "the section's content renders only after the failure branch returned");
  assert.equal(section.split("children(").length - 1, 1, "the content renders once");
  assert.match(section, /return <>\{children\(result\.value\)\}<\/>;\n?$/, "and only from a successful read's value");
  // Each of the four reads reaches the page once, and only as a Section's result.
  const reads = [...page.matchAll(/\bview\.(\w+)/g)].map((m) => m[0]).sort();
  assert.deepEqual(reads, ["view.accounts", "view.audit", "view.drift", "view.unknownUsage"]);
  for (const read of reads) assert.ok(page.includes(`<Section result={${read}}>`), `${read} renders through Section`);
});

// --------------------------------------------------------------------------------------- port

function port(reply: (sent: Sent) => Response) {
  const { api, sent } = recordingApi(reply);
  return { port: apiOperatorPort(async () => api), sent };
}

const ADJUST: OperatorCommand = { action: "adjust_credit", user_id: USER, amount: "-2.50000000" as Credit, ...AUDITED };
const SUSPEND: OperatorCommand = { action: "set_suspension", org_id: ORG, suspended: true, ...AUDITED };
const REVOKE: OperatorCommand = { action: "revoke_key", key_id: KEY_ID, ...AUDITED };

test("U3-P01 each command is exactly one audited operator POST; the actor is never sent (the API derives it)", async () => {
  const cases: [OperatorCommand, string, Record<string, unknown>][] = [
    [ADJUST, "/operator/v1/credit-adjustments", { user_id: USER, amount: "-2.50000000", reason: AUDITED.reason }],
    [SUSPEND, "/operator/v1/suspensions", { org_id: ORG, suspended: true, reason: AUDITED.reason }],
    [REVOKE, "/operator/v1/key-revocations", { key_id: KEY_ID, reason: AUDITED.reason }],
  ];
  for (const [command, path, body] of cases) {
    const { port: p, sent } = port(() => answer(200, { replayed: false }));
    assert.deepEqual(await p.run(command), { ok: true, value: { replayed: false } });
    assert.deepEqual(sent.map((s) => [s.method, s.path, s.body, s.idempotencyKey]), [["POST", path, body, "k-1"]]);
    assert.ok(!JSON.stringify(sent).includes("operator:someone"), "the form-side actor never reaches the API");
  }
});

test("U3-P02 a replay is reported as a replay; an answer without a boolean is 'not confirmed', never success", async () => {
  assert.deepEqual(await port(() => answer(200, { replayed: true, amount: { amount: "5.00000000", unit: "CREDIT" } })).port.run(ADJUST), {
    ok: true,
    value: { replayed: true },
  });
  for (const data of [null, {}, { replayed: "false" }, [{ replayed: false }], "ok"]) {
    const result = await port(() => answer(200, data)).port.run(ADJUST);
    assert.equal(result.ok, false, `answer ${JSON.stringify(data)}`);
    if (!result.ok) assert.equal(result.error.code, "dependency_unavailable");
  }
});

test("U3-P03 the one-time signup grant is not an operator console operation: nothing is sent", async () => {
  const { port: p, sent } = port(() => answer(200, { replayed: false }));
  const result = await p.run({ action: "grant_initial", user_id: USER, ...AUDITED });
  assert.equal(sent.length, 0);
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "unsupported_parameter");
});

test("U3-P04 refusals map to typed codes with fixed text; API detail never reaches the page", async () => {
  const secret = "relation infrx.credit_wallets wallet a1000000 row 7";
  const cases: [number, string, string][] = [
    [403, "forbidden", "forbidden"],
    [401, "invalid_api_key", "forbidden"],
    [409, "idempotency_conflict", "idempotency_conflict"],
    [422, "invalid_request", "invalid_request"],
    [404, "not_found", "not_found"],
    [409, "state_conflict", "state_conflict"],
    [500, "internal_error", "dependency_unavailable"],
    [400, "made_up_code", "dependency_unavailable"],
    [503, "dependency_unavailable", "dependency_unavailable"],
  ];
  for (const [status, code, expected] of cases) {
    const result = await port(() => answer(status, envelope(code, secret))).port.run(ADJUST);
    assert.equal(result.ok, false);
    if (result.ok) continue;
    assert.equal(result.error.code, expected, `${status} ${code}`);
    assert.ok(!result.error.message.includes("a1000000") && !result.error.message.includes("infrx"), result.error.message);
  }
});

test("U3-P05 a transport failure or a client that cannot be built is 'not confirmed, retry with the same key'", async () => {
  for (const p of [
    port(() => {
      throw new Error("fetch failed: 10.0.0.1");
    }).port,
    apiOperatorPort(async () => {
      throw new Error("no cookie");
    }),
  ]) {
    const result = await p.run(SUSPEND);
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.error.code, "dependency_unavailable");
      assert.match(result.error.message, /same idempotency key/);
    }
  }
});

// -------------------------------------------------------------------------------------- reads

const C = (amount: string) => ({ amount, unit: "CREDIT" });
const accountDoc = (over: Record<string, unknown> = {}) => ({
  wallet_id: WALLET,
  user_id: USER,
  org_id: ORG,
  email: "person@example.com",
  suspended: false,
  suspension_reason: null,
  ledger_total: C("10000.00000000"),
  reserved_total: C("14.74560000"),
  available: C("9985.25440000"),
  signup_granted_at: "2026-09-25T10:00:00+00:00",
  ...over,
});
const auditDoc = (over: Record<string, unknown> = {}) => ({
  id: "e1000000-0000-4000-8000-00000000000e",
  at: "2026-09-25T11:00:00+00:00",
  actor: "operator:33333333-3333-4333-8333-333333333333",
  action: "admin_adjust",
  target_org_id: ORG,
  reason: "support ticket 42",
  idempotency_key: "grant_credit:0f",
  ...over,
});
const heldDoc = (over: Record<string, unknown> = {}) => ({
  request_id: "f1000000-0000-4000-8000-00000000000f",
  org_id: ORG,
  created_at: "2026-09-24T09:00:00+00:00",
  reconcile_after: "2026-09-25T09:00:00+00:00",
  hold: C("14.74560000"),
  ...over,
});
type World = Record<string, unknown>;
const WORLD = (): World => ({
  "/operator/v1/accounts": [accountDoc()],
  "/operator/v1/unknown-usage": [heldDoc(), heldDoc({ request_id: "f2000000-0000-4000-8000-00000000000f", hold: { amount: "0.25000000", unit: "USD" } })],
  "/operator/v1/audit": [auditDoc()],
  "/operator/v1/wallet-drift": [],
});
/** The four projections from `world` (a list is a page; a Response is answered as is). */
function reads(world: World) {
  const { api, sent } = recordingApi((s) => {
    const doc = world[s.path.split("?")[0]];
    if (doc === undefined) throw new Error("unreachable");
    return doc instanceof Response ? doc : answer(200, { data: doc, next_cursor: null });
  });
  return { view: operatorReads(api), sent };
}

test("U3-R01 accounts are exact CREDIT strings with their organization's suspension state", async () => {
  const { view } = reads(WORLD());
  assert.deepEqual((await view).accounts, {
    ok: true,
    value: [
      {
        userId: USER,
        orgId: ORG,
        walletId: WALLET,
        email: "person@example.com",
        suspended: false,
        suspensionReason: null,
        ledgerTotal: "10000.00000000",
        reservedTotal: "14.74560000",
        available: "9985.25440000",
        signupGrantedAt: "2026-09-25T10:00:00.000000Z",
      },
    ],
  });
  const suspended = await reads({ ...WORLD(), "/operator/v1/accounts": [accountDoc({ suspended: true, suspension_reason: "other" })] }).view;
  assert.ok(suspended.accounts.ok, "the suspended account");
  assert.deepEqual([suspended.accounts.value[0].suspended, suspended.accounts.value[0].suspensionReason], [true, "other"]);
});

test("U3-R02 a figure that is not an exact CREDIT decimal makes the section unavailable - never a zero", async () => {
  for (const bad of [
    accountDoc({ ledger_total: { amount: "1e4", unit: "CREDIT" } }),
    accountDoc({ available: C("9985.254400001") }),
    accountDoc({ available: { amount: "9985.25440000", unit: "USD" } }),
    accountDoc({ signup_granted_at: "yesterday" }),
  ]) {
    const view = await reads({ ...WORLD(), "/operator/v1/accounts": [bad] }).view;
    assert.equal(view.accounts.ok, false, JSON.stringify(bad));
    if (!view.accounts.ok) assert.equal(view.accounts.error.code, "dependency_unavailable");
    assert.equal(view.audit.ok, true, "one broken section does not take the others down");
  }
});

test("U3-R03 each section fails on its own: a refusal, an outage or an unreadable answer is unavailable", async () => {
  const view = await reads({
    ...WORLD(),
    "/operator/v1/unknown-usage": answer(403, envelope("forbidden", "permission denied for relation")),
    "/operator/v1/wallet-drift": answer(503, envelope("dependency_unavailable", "relation not found")),
    "/operator/v1/audit": new Response("<html>", { status: 502 }),
  }).view.catch(() => assert.fail("one failed section took the whole page down"));
  assert.equal(view.accounts.ok, true);
  for (const section of [view.unknownUsage, view.drift, view.audit]) {
    assert.equal(section.ok, false);
    if (!section.ok) {
      assert.equal(section.error.code, "dependency_unavailable");
      assert.ok(!/permission|relation/.test(section.error.message), "no API text");
    }
  }
});

test("U3-R04 the reads touch only the four operator projections, each bounded", async () => {
  const { view, sent } = reads(WORLD());
  await view;
  assert.deepEqual(sent.map((s) => `${s.method} ${s.path}`).sort(), [
    `GET /operator/v1/accounts?limit=${ACCOUNT_LIMIT}`,
    `GET /operator/v1/audit?limit=${AUDIT_LIMIT}`,
    `GET /operator/v1/unknown-usage?limit=${UNKNOWN_LIMIT}`,
    `GET /operator/v1/wallet-drift?limit=${DRIFT_LIMIT}`,
  ]);
  const empty = await reads({ ...WORLD(), "/operator/v1/accounts": [] }).view;
  assert.deepEqual(empty.accounts, { ok: true, value: [] });
});

test("U3-R05 an unknown-usage hold keeps its own unit: CREDIT for a credit job, USD for a legacy one", async () => {
  const view = await reads(WORLD()).view;
  const at = { orgId: ORG, createdAt: "2026-09-24T09:00:00.000000Z", reconcileAfter: "2026-09-25T09:00:00.000000Z" };
  assert.ok(view.unknownUsage.ok && view.unknownUsage.value[1].hold?.unit === "USD", "a legacy hold stays USD");
  assert.deepEqual(view.unknownUsage, {
    ok: true,
    value: [
      { requestId: "f1000000-0000-4000-8000-00000000000f", ...at, hold: { amount: "14.74560000", unit: "CREDIT" } },
      { requestId: "f2000000-0000-4000-8000-00000000000f", ...at, hold: { amount: "0.25000000", unit: "USD" } },
    ],
  });
  const released = (await reads({ ...WORLD(), "/operator/v1/unknown-usage": [heldDoc({ hold: null })] }).view).unknownUsage;
  assert.ok(released.ok && released.value[0].hold === null, "a released hold is null, not a zero");
  for (const bad of [heldDoc({ hold: { amount: "1e1", unit: "CREDIT" } }), heldDoc({ hold: { amount: "1.00000000", unit: "barter" } }), heldDoc({ created_at: null })]) {
    assert.equal((await reads({ ...WORLD(), "/operator/v1/unknown-usage": [bad] }).view).unknownUsage.ok, false, JSON.stringify(bad));
  }
});

test("U3-R06 the audit trail is the closed action vocabulary; drift rows are exact CREDIT", async () => {
  const view = await reads(WORLD()).view;
  assert.deepEqual(view.audit, {
    ok: true,
    value: [
      {
        id: "e1000000-0000-4000-8000-00000000000e",
        at: "2026-09-25T11:00:00.000000Z",
        actor: "operator:33333333-3333-4333-8333-333333333333",
        action: "admin_adjust",
        targetOrgId: ORG,
        reason: "support ticket 42",
        idempotencyKey: "grant_credit:0f",
      },
    ],
  });
  const drift = [{ wallet_id: WALLET, kind: "consumer", ledger_drift: C("0.00000001"), reserved_drift: C("0.00000000") }];
  const again = await reads({ ...WORLD(), "/operator/v1/audit": [auditDoc({ action: "set_balance" })], "/operator/v1/wallet-drift": drift }).view;
  assert.equal(again.audit.ok, false);
  assert.deepEqual(again.drift, { ok: true, value: [{ walletId: WALLET, kind: "consumer", ledgerDrift: "0.00000001", reservedDrift: "0.00000000" }] });
  const usd = [{ ...drift[0], ledger_drift: { amount: "0.00000001", unit: "USD" } }];
  assert.equal((await reads({ ...WORLD(), "/operator/v1/wallet-drift": usd }).view).drift.ok, false);
});

// -------------------------------------------------------------------------------------- forms

test("U3-F01 a form keeps its idempotency key across every failure and rotates it only after a committed change", () => {
  const fresh = () => "k-2";
  assert.equal(nextKey("k-1", { ok: true, value: { replayed: false } }, fresh), "k-2");
  assert.equal(nextKey("k-1", { ok: true, value: { replayed: true } }, fresh), "k-2");
  for (const code of ["dependency_unavailable", "idempotency_conflict", "forbidden", "invalid_request", "internal_error"] as const) {
    assert.equal(nextKey("k-1", { ok: false, error: { code, message: "x" } }, fresh), "k-1", code);
  }
});

test("U3-F02 outcomes say what committed: once, already applied, refused, or not confirmed", () => {
  assert.deepEqual(outcomeOf({ ok: true, value: { replayed: false } }), { tone: "ok", text: "Committed. The change is recorded once in the audit trail." });
  assert.deepEqual(outcomeOf({ ok: true, value: { replayed: true } }), { tone: "ok", text: "Already applied: this request was recorded earlier, and nothing changed twice." });
  const conflict = outcomeOf({ ok: false, error: { code: "idempotency_conflict", message: "x" } });
  assert.equal(conflict.tone, "error");
  assert.match(conflict.text, /different change/);
  const unconfirmed = outcomeOf({ ok: false, error: { code: "dependency_unavailable", message: "the operator change could not be confirmed; retry with the same idempotency key" } });
  assert.equal(unconfirmed.tone, "error");
  assert.match(unconfirmed.text, /same idempotency key/);
  assert.match(outcomeOf({ ok: false, error: { code: "forbidden", message: "operator authority is required" } }).text, /operator authority/);
});

test("U3-F03 a form submits exactly the allowlisted fields of its operation, plus its key", () => {
  const fields = (entries: Record<string, string>) => (name: string) => entries[name] ?? null;
  assert.deepEqual(formInput("adjust_credit", fields({ user_id: USER, amount: "5.00000000", reason: "goodwill", org_id: ORG, actor: "me" }), "k-1"), {
    action: "adjust_credit",
    user_id: USER,
    amount: "5.00000000",
    reason: "goodwill",
    idempotency_key: "k-1",
  });
  assert.deepEqual(formInput("set_suspension", fields({ org_id: ORG, suspended: "true", reason: "abuse report" }), "k-1"), {
    action: "set_suspension",
    org_id: ORG,
    suspended: true,
    reason: "abuse report",
    idempotency_key: "k-1",
  });
  assert.equal((formInput("set_suspension", fields({ org_id: ORG, suspended: "false", reason: "resolved" }), "k-1") as { suspended: unknown }).suspended, false);
  assert.deepEqual(formInput("revoke_key", fields({ key_id: KEY_ID, reason: "leaked" }), "k-1"), {
    action: "revoke_key",
    key_id: KEY_ID,
    reason: "leaked",
    idempotency_key: "k-1",
  });
});
