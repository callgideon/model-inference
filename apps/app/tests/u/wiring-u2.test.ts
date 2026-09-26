import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const src = (p: string) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
test("U2-W01 the consumer navigation links Settings", () => {
  assert.match(src("components/sidebar.tsx"), /\{ href: "\/settings", label: "Settings", icon: Settings \}/);
});
test("U2-W02 no module remembers or recalls a key's plaintext", () => {
  for (const p of ["lib/keys.ts", "components/snippet.tsx"]) assert.doesNotMatch(src(p), /sessionStorage|rememberKey|recallKey/, p);
});

// WR-U2-3 decided as option (a) at the round-2 merge: the keys page keeps its own copy of the
// P-26 sentence (a re-export from docs/content.ts drags a type-only import of models/catalog.ts
// through I2A's source walk). This guard keeps the two copies from drifting.
import { REVOCATION_COPY as KEYS_REVOCATION_COPY } from "../../app/(console)/api-keys/view-model.ts";
import { REVOCATION_COPY as DOCS_REVOCATION_COPY } from "../../app/(console)/docs/content.ts";
test("U2-W03 the keys page and the Docs carry the same P-26 revocation sentence", () => {
  assert.equal(KEYS_REVOCATION_COPY, DOCS_REVOCATION_COPY);
});
