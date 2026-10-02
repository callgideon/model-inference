// UX-10 pages: the real Releases page, its loading state and the optimization comparison list,
// rendered to static HTML by UX-03's fixture renderer (tests/ux/operate/render.ts) over synthetic
// records, every service state (loading, empty, unavailable, denied, success) per section, and laid
// out in Chromium at 390/768/1440. The page's two ports and the R4 action are stubbed by specifier
// (globalThis.__releases); nothing here reaches a session, a network or a service.
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import type { ReactElement } from "react";
import { html, kit, load, type Membership, type Role } from "../operate/render.ts";
import type { Publication } from "../../../lib/services/releases/port.ts";
import type { Records, Result, Variant } from "../../../lib/services/rollouts/port.ts";
import { REFUSAL_COPY } from "../../../lib/services/rollouts/view.ts";
import { A, AT, DENIED, DEP, dep, DOWN, EXPAND, ident, ok as okDoc, POLICY, pub, records, release, UNMOUNTED, variant } from "./fixtures.ts";

// render.ts registers its hooks on first load; ours, registered after, run first.
await load("lib/services/common.ts");
const STUBS: Record<string, string> = {
  "@/lib/services/releases/port": "export const readPublication = async (actor) => globalThis.__releases.publication(actor);",
  "@/lib/services/rollouts/port": "export const releasesPort = () => globalThis.__releases.rollouts; export const isPreview = () => globalThis.__releases.preview === true;",
  "@/lib/services/rollouts/actions": "export async function proposeRelease() {}",
};
type Hook = { url: string; format?: string; source?: string; shortCircuit?: boolean };
(nodeModule as unknown as { registerHooks(h: object): void }).registerHooks({
  resolve: (s: string, c: object, next: (s: string, c: object) => Hook) => (Object.hasOwn(STUBS, s) ? { url: `releases-stub:${s}`, shortCircuit: true } : next(s, c)),
  load: (u: string, c: object, next: (u: string, c: object) => Hook) =>
    u.startsWith("releases-stub:") ? { format: "module", source: STUBS[u.slice("releases-stub:".length)], shortCircuit: true } : next(u, c),
});

type World = {
  rollouts: { releases(a: unknown): Promise<Result<Records>>; variants(a: unknown): Promise<Result<Variant[]>> };
  publication(actor: { providerId: string }): Promise<Publication>;
  preview?: boolean;
};
const g = globalThis as unknown as { __releases: World; __operateAccess: unknown };
const asWorkspace = (role: Role): Membership => ({ providerId: A, providerName: "Synthetic Lab", role });
const PUBLICATION: Publication = {
  proposals: okDoc({ data: [pub({ state: "approved", decided_at: AT })] }),
  deployments: okDoc({ data: [dep({ visibility: "public" })] }),
  readiness: { [DEP]: UNMOUNTED },
};
const world = (over: Partial<World> = {}): World => ({
  rollouts: { releases: async () => ({ ok: true, value: records([release({ verdict: EXPAND })]) }), variants: async () => ({ ok: true, value: [variant()] }) },
  publication: async () => PUBLICATION,
  ...over,
});
type Page = (p: { searchParams: Promise<Record<string, string>> }) => Promise<ReactElement>;
const { default: Releases } = await load<{ default: Page }>("app/(provider)/releases/page.tsx");
const { default: Loading } = await load<{ default: () => ReactElement }>("app/(provider)/releases/loading.tsx");
const { Variants } = await load<{ Variants: (p: { result: Result<Variant[]> }) => ReactElement }>("app/(provider)/releases/variants.tsx");

async function releases(role: Role, w: World = world(), query: Record<string, string> = {}): Promise<string> {
  const workspace = asWorkspace(role);
  g.__operateAccess = { kind: "ready", workspace, workspaces: [workspace] };
  g.__releases = w;
  return html(await Releases({ searchParams: Promise.resolve(query) }), "/releases");
}
const section = (markup: string, label: string) => {
  const open = markup.indexOf(`<section aria-label="${label}">`); // lab-e2e's harness.section: the exact tag
  return open === -1 ? null : markup.slice(open, markup.indexOf("</section>", open));
};
const states = (markup: string) => [...markup.matchAll(/data-state="(\w+)"/g)].map((m) => m[1]);
const strip = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

const { laidOut, close } = await kit();
test.after(close);

test("UX10-P01 an administrator sees each release's summary, its serving proof and evidence, and proposes against the loaded fence", async () => {
  const page = await releases("administrator");
  assert.match(page, /<h1[^>]*>Releases<\/h1>/);
  const own = section(page, POLICY);
  assert.ok(own !== null, "the release is its own section, labelled by its policy");
  const shown = strip(own);
  for (const fact of ["Candidate healthy in the observation through", "Observed through", "Configured limits", "Candidate p99 (observed)", "Decisions"]) {
    assert.ok(shown.includes(fact), fact);
  }
  assert.match(own, /<details[^>]*>\s*<summary[^>]*>Evidence<\/summary>/, "the full evidence is the detail, under the summary");
  assert.match(own, /lab-badge--success[^>]*>(?:<svg[\s\S]*?<\/svg>)?Candidate healthy</, "the proof is a badge, success only for a healthy observation");
  const forms = [...own.matchAll(/<form[\s\S]*?<\/form>/g)].map((m) => m[0]);
  assert.deepEqual(forms.map(strip).map((t) => t.trim()), ["Propose expansion", "Propose rollback"], "a form's text is its button (lab-e2e E2E-R picks forms by it)");
  for (const f of forms) {
    assert.match(f, /<input type="hidden" name="fence" value="3"\/>/);
    assert.match(f, new RegExp(`<input type="hidden" name="policyRef" value="${POLICY}"/>`));
    assert.doesNotMatch(f, /<input(?![^>]*type="hidden")/, "no editable field: the fence is the one the server loaded");
  }
  const summary = `Propose expanding ${POLICY} at policy revision 3, as shown here.`;
  assert.ok(shown.includes(summary), "the summary precedes the button");
  const id = /<p id="([^"]+)"[^>]*>Propose expanding/.exec(own)?.[1];
  assert.ok(id && own.includes(`aria-describedby="${id}"`), "the button is described by its summary");
  assert.ok(shown.includes("Request —"), "nothing pending reads as R4's dash (lab-e2e E2E-R)");
  assert.doesNotMatch(page, /needs an administrator/);
  assert.match(page, /href="\/optimizations"/, "optimizations are the subordinate comparison view");
});

test("UX10-P02 a viewer and a developer read the same evidence with no proposal and the reason", async () => {
  for (const role of ["viewer", "developer"] as const) {
    const page = await releases(role);
    assert.doesNotMatch(page, /<form/, role);
    assert.ok(strip(page).includes("Proposing an expansion or a rollback needs an administrator."), role);
    assert.ok(section(page, POLICY) !== null, role);
  }
});

test("UX10-P03 publication requests show the operator's decision beside the revision's serving proof, never one for the other", async () => {
  const shown = strip(await releases("viewer"));
  for (const fact of ["Publication requests", "Publish acme/marlin-sop r2", "Approved by an operator", "Serving proof can't be checked right now", "An approval is not proof that this revision is serving now.", "Listed publicly"]) {
    assert.ok(shown.includes(fact), fact);
  }
  const proven = strip(await releases("viewer", world({ publication: async () => ({ ...PUBLICATION, readiness: { [DEP]: okDoc({ deployment_revision_id: DEP, serving_version_id: "sv-1", state: "ready_private", ready: true, reasons: [], checked_at: AT }) } }) })));
  assert.ok(proven.includes("Serving proof current"));
  assert.ok(!proven.includes("An approval is not proof"));
});

test("UX10-P04 every section state: empty, unavailable and denied each name themselves; one failing section leaves the other", async () => {
  const empty = await releases("administrator", world({ rollouts: { ...world().rollouts, releases: async () => ({ ok: true, value: records([]) }) }, publication: async () => ({ ...PUBLICATION, proposals: okDoc({ data: [] }) }) }));
  assert.deepEqual(states(empty), ["empty", "empty"]);
  assert.match(strip(empty), /No releases yet.*No publication requests yet/);
  const down = await releases("administrator", world({ rollouts: { ...world().rollouts, releases: async () => ({ ok: false, reason: "unavailable" }) } }));
  assert.deepEqual(states(down), ["unavailable"], "the release records are unavailable, the publication requests still shown");
  assert.match(down, /role="alert"[^>]*>[\s\S]*?We couldn't load releases/);
  assert.ok(strip(down).includes(REFUSAL_COPY.unavailable), "the R4 refusal copy (lab-e2e E2E-R01)");
  assert.ok(strip(down).includes("Publish acme/marlin-sop r2"));
  assert.doesNotMatch(down, /<form/);
  const denied = await releases("viewer", world({ rollouts: { ...world().rollouts, releases: async () => ({ ok: false, reason: "denied" }) }, publication: async () => ({ ...PUBLICATION, proposals: DENIED }) }));
  assert.deepEqual(states(denied), ["denied", "denied"]);
  const pubDown = await releases("viewer", world({ publication: async () => ({ proposals: DOWN, deployments: DOWN, readiness: {} }) }));
  assert.deepEqual(states(pubDown), ["unavailable"]);
  assert.match(strip(pubDown), /We couldn't load publication requests/);
  for (const page of [empty, down, denied, pubDown]) assert.match(page, /<h1[^>]*>Releases<\/h1>/, "the page keeps its identity in every state");
});

test("UX10-P05 the loading state keeps the heading and announces itself", async () => {
  const page = await html(Loading(), "/releases");
  assert.match(page, /<h1[^>]*>Releases<\/h1>/);
  assert.deepEqual(states(page), ["loading"]);
  assert.match(page, /aria-busy="true"/);
});

test("UX10-P06 ?refused=conflict asks for review again; an unknown reason shows nothing; the preview note only while previewing", async () => {
  const stale = await releases("administrator", world(), { refused: "conflict" });
  assert.match(stale, /<p role="alert">[^<]*newer policy revision[^<]*review the release again/);
  assert.ok(strip(stale).includes(REFUSAL_COPY.conflict), "R4's conflict copy stays (lab-e2e E2E-R)");
  assert.doesNotMatch(await releases("administrator", world(), { refused: "<b>x</b>" }), /<p role="alert">/);
  assert.doesNotMatch(await releases("administrator"), /role="note"/);
  assert.match(await releases("administrator", world({ preview: true })), /<p role="note">Preview: release records/);
});

test("UX10-P07 the optimization list: scoped identities, Not recorded, not comparable, its own serving version; every state", async () => {
  const list = await html(Variants({ result: { ok: true, value: [variant(), variant({ variantRef: "v-old", base: null }), variant({ variantRef: "v-hw", variant: ident({ hardware: "L40S" }) })] } }), "/optimizations");
  const shown = strip(list);
  for (const fact of ["measured: throughput ×1.42", "Not recorded", "Not comparable: an identity is not recorded", "Not comparable: measured on different hardware (B300, L40S)", "is a separate serving version", `sha256:${"e".repeat(64)}`]) {
    assert.ok(shown.includes(fact), fact);
  }
  assert.equal([...list.matchAll(/optimization claimed for this scope only/g)].length, 1, "only the comparable, measured variant claims");
  assert.deepEqual(states(await html(Variants({ result: { ok: true, value: [] } }))), ["empty"]);
  assert.match(strip(await html(Variants({ result: { ok: true, value: [] } }))), /No optimized variants registered yet\./, "lab-e2e E2E-R01's copy");
  assert.deepEqual(states(await html(Variants({ result: { ok: false, reason: "unavailable" } }))), ["unavailable"]);
  assert.deepEqual(states(await html(Variants({ result: { ok: false, reason: "denied" } }))), ["denied"]);
});

test("UX10-L01 long identifiers wrap: at 390, 768 and 1440 nothing scrolls sideways and every control Tab reaches is on screen", async () => {
  await laidOut(await releases("administrator"), 16);
  await laidOut(await html(Variants({ result: { ok: true, value: [variant({ base: null })] } }), "/optimizations"), 4);
});
