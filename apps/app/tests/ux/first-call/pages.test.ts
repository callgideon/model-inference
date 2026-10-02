// node --test "tests/**/*.test.ts"
//
// UX-04 page-level guards, held on the sources (`node --test` cannot render a server component; the
// rendered check is fold.test.ts). Oracles: the guide moved below the model cards or out of step one,
// a Create key button offered to an account that may not create one, a product table/RPC read or a
// fetch other than the catalog in these pages, any browser-storage write in the App, a new route
// handler (a UI inference endpoint), a secret moved into URL state, a /docs# link with no target, or
// Contents out of C-06's order.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import { buildExamples } from "../../../app/(console)/docs/examples.ts";
import { parsePublishedModel } from "../../../lib/contracts/v2/published-model.ts";

const APP = new URL("../../../", import.meta.url).pathname;
const read = (path: string) => readFileSync(join(APP, path), "utf8");
const MODELS = read("app/(console)/models/page.tsx");
const PANEL = read("app/(console)/models/first-call-panel.tsx");
const DOCS = read("app/(console)/docs/page.tsx");
const UI = [
  "app/(console)/models/page.tsx",
  "app/(console)/models/first-call-panel.tsx",
  "app/(console)/models/video-example.tsx",
  "app/(console)/docs/page.tsx",
  "components/snippet.tsx",
];

/** Every non-test source file under the App's code roots. */
function sources(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.(ts|tsx)$/.test(name) && !/\.test\.ts$/.test(name)) out.push(relative(APP, path));
    }
  };
  for (const root of ["app", "components", "lib"]) walk(join(APP, root));
  return out;
}

test("the first-call guide renders before any model card, with the key dialog as step one", () => {
  const panel = MODELS.indexOf("<FirstCallPanel");
  assert.ok(panel !== -1, "the Models page has no first-call guide");
  assert.ok(panel < MODELS.indexOf("catalog.models.map((model) =>"), "the guide is below the model cards");
  const [one, two] = [PANEL.indexOf("<Step n={1}"), PANEL.indexOf("<Step n={2}")];
  const slot = PANEL.indexOf("{createKey}");
  assert.ok(one !== -1 && one < slot && slot < two, "the key dialog is not in step one");
  assert.ok(slot < PANEL.indexOf("<Snippet"), "code comes before the key step");
});

test("Create key is offered only to an account that may create a key, through the existing dialog", () => {
  assert.match(MODELS, /createKey=\{guide\.canCreate \? <CreateKeyDialog \/> : null\}/);
  assert.match(MODELS, /import \{ CreateKeyDialog \} from "\.\.\/api-keys\/create-key-dialog";/);
});

test("these pages read no product table and fetch nothing but the published catalog", () => {
  for (const path of UI) {
    const source = read(path);
    assert.doesNotMatch(source, /\.from\(|\.rpc\(|createClient|supabase|fetch\(/, `${path} reads a table or fetches`);
  }
  assert.match(MODELS, /await consumerSession\(\)/, "the guide does not use the scoped consumer port");
});

test("nothing in the App writes to browser storage, and no secret enters URL state", () => {
  for (const path of sources()) {
    assert.doesNotMatch(read(path), /localStorage|sessionStorage|indexedDB|document\.cookie/, `${path} touches browser storage`);
  }
  for (const path of [...UI, "app/(console)/api-keys/create-key-dialog.tsx"]) {
    assert.doesNotMatch(read(path), /router\.(push|replace)\(|URLSearchParams|searchParams|history\.(push|replace)State/, `${path} writes URL state`);
  }
});

test("no new route handler: the console adds no UI inference endpoint", () => {
  const handlers = sources().filter((path) => /(^|\/)route\.ts$/.test(path)).sort();
  assert.deepEqual(handlers, [
    "app/(console)/usage/[requestId]/result/route.ts",
    "app/api/client-errors/route.ts",
    "app/api/version/route.ts",
    "app/auth/callback/route.ts",
    // AP-09: clears an ended session's cookie and redirects to sign-in; no inference.
    "app/auth/expired/route.ts",
  ]);
});

const PUBLISHED = new URL("../../../../infrx-api/infrx/contracts/v2/published/published_marlin_credit.json", import.meta.url);
const EXAMPLE_IDS = buildExamples(parsePublishedModel(JSON.parse(readFileSync(PUBLISHED, "utf8")))).map((e) => e.id);
// The example sections take their ids from the generator; every other anchor is written in the page.
const DOC_IDS = new Set([...[...DOCS.matchAll(/ id="([a-z-]+)"/g)].map((m) => m[1]), ...EXAMPLE_IDS]);

test("every /docs anchor link in the App, Settings included, lands on a Docs section", () => {
  const links = sources().flatMap((path) => [...read(path).matchAll(/\/docs#([a-z-]+)/g)].map((m) => ({ path, id: m[1] })));
  assert.ok(links.some((l) => l.path.startsWith("app/(console)/settings/")), "Settings no longer links into Docs");
  for (const { path, id } of links) assert.ok(DOC_IDS.has(id), `${path} links to /docs#${id}, which is not a section`);
  for (const id of ["limits", "streaming", "retries", "pricing", "retention", "keys", "prompts", "support", "upload", "async"]) {
    assert.ok(DOC_IDS.has(id), `the existing anchor #${id} is gone`);
  }
});

test("Contents follows C-06's order and each entry is a section, in that order on the page", () => {
  const contents = [...DOCS.matchAll(/^ {2}\["([a-z-]+)", "[^"]+"\],$/gm)].map((m) => m[1]);
  assert.deepEqual(contents, ["quickstart", "video", "async", "stream", "retries", "limits", "pricing", "retention", "keys", "errors"]);
  const at = (id: string) => {
    const literal = DOCS.indexOf(`id="${id}"`);
    return literal !== -1 ? literal : DOCS.indexOf(`{example("${id}")}`);
  };
  const positions = contents.map(at);
  assert.ok(positions.every((p) => p !== -1), `a Contents entry has no section: ${contents.filter((_, i) => positions[i] === -1)}`);
  assert.deepEqual([...positions].sort((a, b) => a - b), positions, "the sections are not in Contents order");
  assert.match(DOCS, /<details className="[^"]*lg:hidden">\s*<summary[^>]*>Contents<\/summary>/, "no mobile Contents disclosure");
  assert.match(DOCS, /<nav aria-label="Contents" className="[^"]*hidden lg:block">/, "no desktop section navigation");
});
