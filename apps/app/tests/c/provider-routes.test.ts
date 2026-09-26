// node --test "tests/**/*.test.ts"
//
// E3A F-2: /traces, /dedicated and /teams rendered for a signed-in consumer (200) while the sidebar only
// hid them. Brief 04 ("Shared consumer boundary"): provider routes stay out of consumer navigation AND
// protected. One guard decides (`providerRoute`, lib/services/console.ts): anything but an operator flag
// of exactly `true` is a 404; an operator keeps the preview until V1M moves it to Lab. The pages are
// `.tsx` and cannot load under node --test, so their wiring is pinned as source (as U3-S02 does).
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import orgs from "../../lib/contracts/fixtures/orgs.json" with { type: "json" };
import { providerRoute } from "../../lib/services/console.ts";

const CONSOLE = join(resolve(dirname(fileURLToPath(import.meta.url)), "../.."), "app", "(console)");
const PAGES = ["traces", "dedicated", "teams"];
const NOT_FOUND = new Error("NEXT_HTTP_ERROR_FALLBACK;404");
const notFound = (): never => {
  throw NOT_FOUND;
};
const refused = (session: { isOperator: boolean }) => {
  try {
    providerRoute(session, notFound);
    return false;
  } catch (error) {
    assert.equal(error, NOT_FOUND, "the refusal is notFound(), nothing else");
    return true;
  }
};

test("C-PROV-01 a consumer gets a 404 from the provider-route guard; an operator is let through", () => {
  const sessions = Object.values(orgs.sessions);
  assert.ok(sessions.some((s) => s.isOperator) && sessions.some((s) => !s.isOperator), "the fixture has both");
  for (const session of sessions) {
    assert.equal(refused(session), !session.isOperator, `${session.email} (isOperator=${session.isOperator})`);
  }
  for (const value of [1, "true", {}, undefined, null]) {
    assert.ok(refused({ isOperator: value as unknown as boolean }), `isOperator=${JSON.stringify(value)} is not authority`);
  }
});

test("C-PROV-02 each provider page runs the guard first, before any read", () => {
  for (const name of PAGES) {
    const page = readFileSync(join(CONSOLE, name, "page.tsx"), "utf8");
    assert.match(page, /^import \{ notFound \} from "next\/navigation";$/m, `${name}: notFound from next/navigation`);
    assert.match(page, /^import \{ providerRoute \} from "@\/lib\/services\/console";$/m, `${name}: the shared guard`);
    const body = /export default (?:async )?function \w+\([^)]*\) \{\n([\s\S]*)$/.exec(page);
    assert.ok(body, `${name}: no default page function`);
    // The session (React-cached, the layout's own read) is the only thing allowed before the guard.
    assert.match(
      body[1],
      /^  (?:providerRoute\(await getSession\(\), notFound\);|const session = await getSession\(\);\n  providerRoute\(session, notFound\);)\n/,
      `${name}: the guard is the page's first statement`,
    );
    assert.equal(body[1].split("providerRoute(").length, 2, `${name}: one guard call`);
    // A loading.tsx wraps the page in Suspense: the shell streams with 200 before notFound() runs, so
    // the consumer got a 404 body under a 200 status (E3A journey, /traces).
    assert.ok(!existsSync(join(CONSOLE, name, "loading.tsx")), `${name}: no loading boundary above the guard`);
  }
});
