// node --test "tests/**/*.test.ts"
//
// U2: what the API Keys and Settings pages' own source may and may not do. These are the seams a
// view model cannot see: where the plaintext secret goes, which action a control calls, and whether
// a setting is a real control. `.tsx` cannot be loaded by `node --test` (R48), so they are read as
// source. Failure oracle per case: the old page kept the plaintext in sessionStorage, called a
// local action that returned database `error.message` verbatim and skipped verification/wallet
// checks, told the individual revocation takes "within a minute", and rendered a failed key read
// as "No keys yet" — each fails a case here. Fix round (review 0-U2-V-1..5): the page must render
// the model's unavailable verdict, the plaintext has an allowlist of uses instead of a cap, the
// copy is pinned where it renders, and the action call shapes are exact.
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
const count = (src: string, text: string) => src.split(text).length - 1;

/** The source strictly between `start` (which must appear once) and the next `end`. */
function between(src: string, start: string, end: string): string {
  assert.equal(count(src, start), 1, `exactly one \`${start}\``);
  const from = src.indexOf(start) + start.length;
  const to = src.indexOf(end, from);
  assert.ok(to > from, `\`${end}\` follows \`${start}\``);
  return src.slice(from, to);
}

/** `word` is used only at these exact sites, each once. An allowlist: any other use is a new place it can go. */
function onlyAt(src: string, word: RegExp, sites: string[], label: string) {
  let rest = src;
  for (const site of sites) {
    assert.equal(count(rest, site), 1, `${label}: expected exactly one \`${site}\``);
    rest = rest.replace(site, "");
  }
  assert.doesNotMatch(rest, word, `${label}: used somewhere not on the allowlist`);
}

export const T = {
  secret: "U2-S01 the plaintext secret is never stored, logged, put in a URL or sent anywhere but the screen",
  actions: "U2-S02 the keys controls call the shared C3A actions; the leaky page-local actions are gone",
  read: "U2-S03 the keys page reads through the consumer session and never turns a failed read into an empty list",
  settings: "U2-S04 settings has no fake controls: nothing on it saves, toggles or posts",
  keyboard: "U2-S05 every icon-only control has an accessible name and the name field has a label",
  copy: "U2-S06 the one-time and lost-key copy is on screen where it applies, and closing the dialog forgets the plaintext",
};

test(T.secret, () => {
  const files = [...owned(KEYS), ...owned(SETTINGS)];
  assert.ok(files.length >= 4, "the keys and settings sources must exist");
  for (const { f, src } of files) {
    assert.doesNotMatch(src, /\bconsole\.(log|info|warn|error|debug)\b/, `${f}: nothing on these pages logs`);
    assert.doesNotMatch(src, /\b(sessionStorage|localStorage|indexedDB|document\.cookie)\b/, `${f}: no browser storage`);
    assert.doesNotMatch(src, /\brememberKey\b/, `${f}: the plaintext is not remembered for later redisplay`);
    assert.doesNotMatch(src, /URLSearchParams|searchParams|router\.(push|replace)\(|location\.(href|assign|replace)|\bhistory\.|window\.open/, `${f}: no URL carries a key`);
    assert.doesNotMatch(
      src,
      /\bfetch\(|sendBeacon|XMLHttpRequest|new Image\b|WebSocket|EventSource|postMessage|navigator\.share|analytics|posthog|sentry|gtag/i,
      `${f}: no network or analytics call`,
    );
    assert.doesNotMatch(src, /toast[^;]*\bsecret\b/, `${f}: the plaintext is never in a toast`);
  }
  const dialog = read(KEYS, "create-key-dialog.tsx");
  // Allowlist, not a cap: the plaintext is the create outcome's, held in state, rendered once and
  // copied to the clipboard. Any other use of it, or of the outcome that carries it, fails here.
  onlyAt(
    dialog,
    /\bsecret\b/,
    [
      "const [secret, setSecret] = useState<string | null>(null);",
      'if (outcome.kind === "secret") setSecret(outcome.secret);',
      "if (!secret) return;",
      "await navigator.clipboard.writeText(secret);",
      "{secret ? (",
      ">{secret}</code>",
    ],
    "the plaintext",
  );
  onlyAt(
    dialog,
    /\boutcome\b/,
    [
      "const outcome = createOutcome(await settle(() => createConsumerKey({ name, idempotency_key: attempt }), CREATE_LOST));",
      'if (outcome.kind === "secret") setSecret(outcome.secret);',
      "else setError(outcome.message);",
    ],
    "the create outcome",
  );
  // State is only ever set to the outcome's plaintext or cleared.
  onlyAt(dialog, /\bsetSecret\(/, ["setSecret(outcome.secret)", "setSecret(null)"], "setSecret");
});

test(T.actions, () => {
  assert.equal(existsSync(join(KEYS, "actions.ts")), false, "the page-local actions (DB error text, no verification gate) are deleted");
  const dialog = read(KEYS, "create-key-dialog.tsx");
  const revoke = read(KEYS, "revoke-button.tsx");
  assert.match(dialog, /import \{[^}]*\bcreateConsumerKey\b[^}]*\} from "@\/app\/actions"/);
  assert.match(revoke, /import \{[^}]*\brevokeConsumerKey\b[^}]*\} from "@\/app\/actions"/);
  // One idempotency key per opened dialog, renewed on close: a double submit replays, never mints twice.
  assert.match(dialog, /setAttempt\(crypto\.randomUUID\(\)\)/);
  // Exact call shapes: the name and this dialog's idempotency key and nothing else (a trace_mode is
  // refused by C3A); the row's id, never its name. Each call is wrapped in `settle`, so a rejected
  // server action ends in a fixed notice instead of a spinner that never stops (K07).
  assert.equal(count(dialog, "createConsumerKey("), 1, "one create call");
  assert.match(dialog, /createOutcome\(await settle\(\(\) => createConsumerKey\(\{ name, idempotency_key: attempt \}\), CREATE_LOST\)\)/);
  assert.equal(count(revoke, "revokeConsumerKey("), 1, "one revoke call");
  assert.match(revoke, /const result = await settle\(\(\) => revokeConsumerKey\(id\), REVOKE_LOST\);/);
  // The page binds each row's control to that row's id.
  assert.match(read(KEYS, "page.tsx"), /<RevokeButton id=\{k\.id\} name=\{k\.name\} \/>/);
  // Revoke asks first, with the tested confirmation text.
  assert.match(revoke, /if \(pending \|\| !confirm\(revokeConfirmText\(name\)\)\) return;/);
  assert.doesNotMatch(revoke, /within a minute/, "P-26: new requests are refused immediately, not within a minute");
  // Both controls re-read committed state: revoke unconditionally after the settled call (a failed or
  // unconfirmed revoke may still have landed), the dialog when it closes.
  const revokeBody = between(revoke, "async function revoke() {", "\n  }\n");
  assert.match(revokeBody, /REVOKE_LOST\);\n    setPending\(false\);\n[\s\S]*\n    router\.refresh\(\);$/, "refresh is the last, unconditional step");
  assert.match(between(dialog, "if (!next) {", "\n    }\n"), /router\.refresh\(\);/);
  // Pending is cleared right after the settled call, before anything that could branch.
  assert.match(dialog, /CREATE_LOST\)\);\n    setPending\(false\);/);
});

test(T.read, () => {
  const page = read(KEYS, "page.tsx");
  assert.match(page, /consumerSession\(\)/, "the individual's own consumer context and read port (C0)");
  assert.match(page, /keysPageModel\(/, "every state is decided by the tested model");
  assert.doesNotMatch(page, /getSession\(\)|createClient\(|\.from\("api_keys"\)/, "no first-membership session and no direct table read");
  assert.doesNotMatch(page, /data \?\? \[\]/, "a failed read is not an empty list");
  // The page renders the model's verdict: the unavailable branch shows the model's message and a
  // retry, and "No keys" is said once, only in the empty branch.
  const unavailable = between(page, 'model.list.kind === "unavailable" ? (', ') : model.list.kind === "empty" ? (');
  assert.match(unavailable, /<p>\{model\.list\.message\}<\/p>/);
  assert.match(unavailable, /\{model\.list\.retry \? \(\s*<a [^>]*href="\/api-keys"[^>]*>\s*Try again\s*<\/a>/);
  assert.doesNotMatch(unavailable, /no keys/i);
  assert.equal((page.match(/no keys/gi) ?? []).length, 1, "the empty-list text appears once");
  assert.match(between(page, 'model.list.kind === "empty" ? (', ") : ("), /No keys yet\./);
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

test(T.copy, () => {
  const dialog = read(KEYS, "create-key-dialog.tsx");
  const page = read(KEYS, "page.tsx");
  // The one-time warning is shown with the plaintext, in the same branch.
  const shown = between(dialog, "{secret ? (", ") : (");
  assert.match(shown, /<DialogDescription>\{ONE_TIME_COPY\}<\/DialogDescription>/);
  assert.match(shown, />\{secret\}<\/code>/);
  // The page says how to rotate a lost key and what revocation does.
  assert.equal(count(page, "<p>{LOST_KEY_COPY}</p>"), 1);
  assert.equal(count(page, "<p>{REVOCATION_COPY}</p>"), 1);
  // Closing the dialog, by Done, Escape or the backdrop, forgets the plaintext.
  assert.match(dialog, /<Dialog open=\{open\} onOpenChange=\{reset\}>/);
  assert.match(dialog, /onClick=\{\(\) => reset\(false\)\}/);
  assert.match(between(dialog, "if (!next) {", "\n    }\n"), /\n      setSecret\(null\);\n/);
});
