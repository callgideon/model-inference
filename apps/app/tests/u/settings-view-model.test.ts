// node --test "tests/**/*.test.ts"
//
// U2: the Settings page model (`app/(console)/settings/view-model.ts`). v1 has no mutable consumer
// setting (P-09: consumer capture is off; C3A exposes no settings write), so every privacy row is a
// fact with its availability stated, never a control. Failure oracles: a row that offers a toggle,
// a claim of zero retention / "never stored" / a 120-second window, trace-off presented as deleting
// serving data, or an account that failed to load shown as a blank or a guessed e-mail.
import assert from "node:assert/strict";
import test from "node:test";

import { settingsModel } from "../../app/(console)/settings/view-model.ts";

const ACCOUNT = { userId: "u", email: "a@example.com", walletId: "w", orgId: "o", suspended: false };

export const T = {
  facts: "U2-P01 every privacy row is a fixed fact with its availability, and none is a control",
  truthful: "U2-P02 privacy copy states real serving retention and makes no zero-retention, never-stored or 120-second claim",
  consent: "U2-P03 sharing, annotation, evaluation and training are not offered, and signup grants no such permission",
  account: "U2-P04 the account block shows the session's own e-mail and state; a failed load says so",
};

const all = (m: ReturnType<typeof settingsModel>) => JSON.stringify(m);

test(T.facts, () => {
  const model = settingsModel({ state: "ready", account: ACCOUNT });
  assert.ok(model.privacy.length >= 4);
  for (const row of model.privacy) {
    assert.deepEqual(Object.keys(row).sort(), ["detail", "href", "status", "title"], `${row.title}: a fact, not a control`);
    assert.ok(["Off", "Not offered", "Not available", "Stored for limited periods"].includes(row.status), `${row.title}: ${row.status}`);
  }
  const capture = model.privacy.find((r) => /trace capture/i.test(r.title));
  assert.equal(capture?.status, "Off");
  assert.match(capture?.detail ?? "", /cannot be turned on/i);
});

test(T.truthful, () => {
  const text = all(settingsModel({ state: "ready", account: ACCOUNT }));
  assert.match(text, /This is not zero data retention\./);
  const claims = text.replaceAll("This is not zero data retention.", "");
  assert.doesNotMatch(claims, /\bZDR\b|zero (data )?retention|120[- ]?s\b|120 seconds|never stor|not stored|no data is stored/i);
  const retention = settingsModel({ state: "ready", account: ACCOUNT }).privacy.find((r) => /retention/i.test(r.title));
  assert.equal(retention?.href, "/docs#retention", "the periods are the published record's, read on Docs");
  assert.match(retention?.detail ?? "", /video/);
  // Turning capture off is not deletion of what serving keeps.
  assert.match(text, /does not change what (we|serving) store/i);
});

test(T.consent, () => {
  const model = settingsModel({ state: "ready", account: ACCOUNT });
  const use = model.privacy.find((r) => /training/i.test(r.title));
  assert.equal(use?.status, "Not offered");
  assert.match(use?.detail ?? "", /signing up grants no/i);
  assert.doesNotMatch(all(model), /opt(ed)? in by default|pre-?checked|you agreed/i);
});

test(T.account, () => {
  const ready = settingsModel({ state: "ready", account: ACCOUNT }).account;
  assert.deepEqual(ready, { kind: "ready", email: "a@example.com", status: "Verified", suspended: false });
  const suspended = settingsModel({ state: "ready", account: { ...ACCOUNT, suspended: true } }).account;
  assert.equal(suspended.kind === "ready" && suspended.suspended, true);
  const unverified = settingsModel({ state: "unverified", userId: "u", email: "b@example.com" }).account;
  assert.deepEqual(unverified, { kind: "ready", email: "b@example.com", status: "Not verified", suspended: false });
  const onboarding = settingsModel({ state: "onboarding", userId: "u", email: "c@example.com" }).account;
  assert.equal(onboarding.kind === "ready" && onboarding.status, "Verified, finishing setup");
  for (const context of [{ state: "unavailable" as const }, { state: "signed_out" as const }]) {
    const account = settingsModel(context).account;
    assert.equal(account.kind, "unavailable");
    assert.doesNotMatch(JSON.stringify(account), /@/, "no guessed e-mail");
  }
  // Privacy facts do not depend on the account read: they are still stated when it failed.
  assert.equal(settingsModel({ state: "unavailable" }).privacy.length, settingsModel({ state: "ready", account: ACCOUNT }).privacy.length);
});
