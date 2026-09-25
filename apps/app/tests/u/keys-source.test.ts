// node --test "tests/**/*.test.ts"
//
// U2: what the API Keys and Settings pages' own source may and may not do. These are the seams a
// view model cannot see: where the plaintext secret goes, which action a control calls, and whether
// a setting is a real control. `.tsx` cannot be loaded by `node --test` (R48), so they are read as
// source. Failure oracle per case: the old page kept the plaintext in sessionStorage, called a
// local action that returned database `error.message` verbatim and skipped verification/wallet
// checks, told the individual revocation takes "within a minute", and rendered a failed key read
// as "No keys yet" — each fails a case here.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const app = join(dirname(fileURLToPath(import.meta.url)), "../..");
const KEYS = join(app, "app/(console)/api-keys");
const SETTINGS = join(app, "app/(console)/settings");
const read = (dir: string, file: string) => readFileSync(join(dir, file), "utf8");
const owned = (dir: string) =>
  existsSync(dir) ? readdirSync(dir).filter((f) => /\.tsx?$/.test(f)).map((f) => ({ f, src: read(dir, f) })) : [];

export const T = {
  secret: "U2-S01 the plaintext secret is never stored, logged, put in a URL or sent anywhere but the screen",
  actions: "U2-S02 the keys controls call the shared C3A actions; the leaky page-local actions are gone",
  read: "U2-S03 the keys page reads through the consumer session and never turns a failed read into an empty list",
  settings: "U2-S04 settings has no fake controls: nothing on it saves, toggles or posts",
  keyboard: "U2-S05 every icon-only control has an accessible name and the name field has a label",
};

test(T.secret, () => {
  const files = [...owned(KEYS), ...owned(SETTINGS)];
  assert.ok(files.length >= 4, "the keys and settings sources must exist");
  for (const { f, src } of files) {
    assert.doesNotMatch(src, /\bconsole\.(log|info|warn|error|debug)\b/, `${f}: nothing on these pages logs`);
    assert.doesNotMatch(src, /\b(sessionStorage|localStorage|indexedDB|document\.cookie)\b/, `${f}: no browser storage`);
    assert.doesNotMatch(src, /\brememberKey\b/, `${f}: the plaintext is not remembered for later redisplay`);
    assert.doesNotMatch(src, /URLSearchParams|searchParams|router\.(push|replace)\(|location\.(href|assign)/, `${f}: no URL carries a key`);
    assert.doesNotMatch(src, /\bfetch\(|sendBeacon|analytics|posthog|sentry|gtag/i, `${f}: no network or analytics call`);
  }
  const dialog = read(KEYS, "create-key-dialog.tsx");
  // The secret reaches exactly three places: the state that renders it, the clipboard, and the reset.
  const uses = dialog.match(/\bsecret\b/g) ?? [];
  assert.ok(uses.length > 0 && uses.length <= 8, `the secret is referenced ${uses.length} times; each new use is a new place it can leak`);
  assert.match(dialog, /navigator\.clipboard\.writeText\(secret\)/, "copying is the only thing done with it");
});

test(T.actions, () => {
  assert.equal(existsSync(join(KEYS, "actions.ts")), false, "the page-local actions (DB error text, no verification gate) are deleted");
  const dialog = read(KEYS, "create-key-dialog.tsx");
  const revoke = read(KEYS, "revoke-button.tsx");
  assert.match(dialog, /import \{[^}]*\bcreateConsumerKey\b[^}]*\} from "@\/app\/actions"/);
  assert.match(revoke, /import \{[^}]*\brevokeConsumerKey\b[^}]*\} from "@\/app\/actions"/);
  // One idempotency key per opened dialog, renewed on close: a double submit replays, never mints twice.
  assert.match(dialog, /idempotency_key: attempt/);
  assert.match(dialog, /setAttempt\(crypto\.randomUUID\(\)\)/);
  // The outcome and the confirmation text come from the tested view model, not ad-hoc strings.
  assert.match(dialog, /createOutcome\(/);
  assert.match(revoke, /revokeConfirmText\(/);
  assert.doesNotMatch(revoke, /within a minute/, "P-26: new requests are refused immediately, not within a minute");
});

test(T.read, () => {
  const page = read(KEYS, "page.tsx");
  assert.match(page, /consumerSession\(\)/, "the individual's own consumer context and read port (C0)");
  assert.match(page, /keysPageModel\(/, "every state is decided by the tested model");
  assert.doesNotMatch(page, /getSession\(\)|createClient\(|\.from\("api_keys"\)/, "no first-membership session and no direct table read");
  assert.doesNotMatch(page, /data \?\? \[\]/, "a failed read is not an empty list");
});

test(T.settings, () => {
  const files = owned(SETTINGS);
  assert.ok(files.some((x) => x.f === "page.tsx"), "the settings page exists");
  for (const { f, src } of files) {
    assert.doesNotMatch(src, /type="checkbox"|<Switch|<Checkbox|role="switch"|onChange=|onCheckedChange|<form|"use server"|"use client"/, `${f}: no control that looks like it saves`);
  }
});

test(T.keyboard, () => {
  const dialog = read(KEYS, "create-key-dialog.tsx");
  const revoke = read(KEYS, "revoke-button.tsx");
  assert.match(dialog, /<Label htmlFor="key-name">/);
  assert.match(dialog, /id="key-name"/);
  assert.match(dialog, /aria-label="Copy key"/);
  assert.match(revoke, /aria-label=\{`Revoke \$\{name\}`\}/);
  // Native buttons only: a clickable div or span is unreachable by keyboard.
  for (const src of [dialog, revoke, read(KEYS, "page.tsx")]) assert.doesNotMatch(src, /<(div|span)[^>]*onClick=/);
});
