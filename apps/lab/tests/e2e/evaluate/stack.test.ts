// LAB-E2E evaluate = E6L j10: the provider UI launches, compares and cancels. The Lab's evaluation pages,
// served by the built Lab app and signed in through its own form, over lab-api's /lab/v1/evaluations
// (LAB_EVALS as the gateway composes it: D7, B1's freeze and L2 real) on key l4 (backend.py): a launch
// through the page's form, progress and a cancel from the records, the comparison with its slices and
// uncertainty, and the unsafe variants. E02-E05 drive the launch form over the unit's own ports: they
// run only while `world.composed.catalog` is true (0066's lab_eval_catalog answers through WR-UXVF-1's
// caller) and the journey composition swaps no fake in (its stand_ins name none, WR-UXVF-2); otherwise
// they are reported TODO "NOT RUN[...]" and run nothing - never a pass over the route suite's fakes
// (TODO, not SKIP: gate.py's `missing` reads a skipped case as a red suite; the cell is NOT RUN through
// the record's `composed`). Skipped unless LAB_E2E_REAL=1 (Docker, l4):
//   cd apps/lab && LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test tests/e2e/evaluate/stack.test.ts
import assert from "node:assert/strict";
import test from "node:test";
import { ACCESS_COPY } from "../../../lib/auth/access.ts";
import { comparison, REFUSAL_COPY } from "../../../lib/services/evaluation/view.ts";
import { REPORTS } from "../../b/real.ts";
import { door, form, forms, record, SKIP, stack, type Browser } from "../harness.ts";

type World = { A: string; B: string; dataset: string; servings: [string, string]; users: Record<string, string>; composed: Record<string, boolean>; stand_ins: string[] };
const LAUNCH = { seed: "7", max_cases: "3", run_limit: "10", metric_source: "deterministic_metric", confidence: "0.95", margin: "0.05", min_cases: "2", slices: "safety 0 2" };

test("E2E-E j10 the provider UI launches, compares and cancels", { skip: SKIP }, async (t) => {
  const s = await stack<World>("evaluate");
  t.after(s.stop);
  const w = s.world;
  const as = async (who: string): Promise<Browser> => {
    const b = s.browser();
    assert.equal((await b.signIn(`${who}@lab.e2e`)).location, "/", `${who} signs in`);
    return b;
  };
  const dev = await as("dev");
  const runRowOf = (page: string, id: string) => new RegExp(`lab:run:${w.A}:${id}@sha256:[0-9a-f]{64} (\\S+) `).exec(page)?.[1] ?? null;
  let experiment = "";
  let runs: string[] = [];
  /** A launch-form case: run only over the ports the unit's own composition carries (backend.carried) -
   * while the journey swaps the route suite's fakes in for any port (its stand_ins say so), a pass would
   * prove the fakes, not the unit. */
  const faked = w.stand_ins.filter((s) => s.includes("the route suite's fakes"));
  const notRun = !w.composed.catalog
    ? "NOT RUN[SR-AP10-1]: the composed catalog answers 503 (0066's lab_eval_catalog through Catalog.catalog, WR-UXVF-1)"
    : faked.length > 0
      ? `NOT RUN[WR-UXVF-2]: the journey composition swaps fakes in for ${faked.join("; ")}`
      : null;
  const launching = (name: string, fn: () => Promise<void>) => (notRun === null ? t.test(name, fn) : t.test(name, { todo: notRun }));

  await t.test("E2E-E01 as the gateway composes LAB_EVALS, the records are listed and the launch offers the provider's own catalog", async () => {
    await door(s.api, "composition", { as: "gateway" });
    const page = await dev.get("/evaluations");
    // UX-08: the catalog is its own section; 0066's listing (the seeded dataset, the two ready private dev
    // servings) fills the launch form, and the records (AP-10's composed experiments and D7's runs) show
    assert.ok(!page.text.includes(REFUSAL_COPY.unavailable), "the records are read");
    assert.ok(page.text.includes("No experiments yet.") && page.text.includes("No runs yet."), page.text);
    assert.ok(!page.text.includes("Comparisons cannot be launched right now"), "the catalog answers");
    assert.ok(!page.text.includes("Nothing to compare yet"), "the catalog offers what the world seeded");
    const launch = form(page.html, /Queue baseline and candidate runs/);
    for (const ref of [w.dataset, ...w.servings]) assert.ok(page.html.includes(`value="${ref}"`), `the form offers ${ref}`);
    assert.deepEqual(launch.fields.filter(([k]) => k.endsWith("serving_ref")), [["baseline_serving_ref", w.servings[0]], ["candidate_serving_ref", w.servings[0]]]);
    await door(s.api, "composition", { as: "journey" });
  });

  await launching("E2E-E02 a launch through the page's form lands on the experiment: its declared protocol and two queued runs", async () => {
    const page = await dev.get("/evaluations");
    assert.ok(page.text.includes("No experiments yet.") && page.text.includes("No runs yet."));
    const launched = await dev.submit("/evaluations", form(page.html, /Queue baseline and candidate runs/), { ...LAUNCH, candidate_serving_ref: w.servings[1] });
    assert.match(launched.location ?? "", /^\/experiments\/[0-9a-f-]{36}$/);
    experiment = launched.location!.split("/")[2];
    const shown = await dev.get(launched.location!);
    assert.ok(shown.text.includes(`Experiment ${experiment}`));
    assert.ok(shown.text.includes("0.95 confidence · margin 0.05 · at least 2 paired cases · deterministic metric"));
    assert.ok(shown.text.includes("slice safety: margin 0, at least 2 cases"));
    runs = [...shown.html.matchAll(new RegExp(`<input type="hidden" name="run_id" value="([0-9a-f-]{36})"`, "g"))].map((m) => m[1]);
    assert.equal(runs.length, 2, "both runs are queued and cancellable");
    assert.deepEqual(runs.map((r) => runRowOf(shown.text, r)), ["queued", "queued"]);
    assert.ok(shown.text.includes("Pending: B2 compares the runs once both have ended."));
  });

  await launching("E2E-E03 progress and a cancel come from the records: a running run is cancelled once, a finished run offers none", async () => {
    const [base, cand] = runs;
    await door(s.api, "state", { run_id: cand, state: "running" });
    const running = await dev.get("/evaluations");
    assert.equal(runRowOf(running.text, cand), "running");
    const cancel = forms(running.html).find((f) => f.fields.some(([k, v]) => k === "run_id" && v === cand))!;
    assert.equal((await dev.submit("/evaluations", cancel)).location, "/evaluations");
    const after = await dev.get("/evaluations");
    assert.equal(runRowOf(after.text, cand), "cancelled");
    assert.ok(!after.html.includes(`name="run_id" value="${cand}"`), "a cancelled run offers no cancel");
    assert.equal((await dev.submit("/evaluations", cancel)).location, "/evaluations?refused=conflict", "cancelling again is a conflict");
    await door(s.api, "state", { run_id: base, state: "running" });
    await door(s.api, "state", { run_id: base, state: "succeeded" });
    const done = await dev.get("/evaluations");
    assert.equal(runRowOf(done.text, base), "finished", "succeeded, in the view's words");
    assert.ok(!done.html.includes(`name="run_id" value="${base}"`), "a finished run offers no cancel");
  });

  await launching("E2E-E04 the comparison is B2's stored report as the view reads it: outcome, reasons, every estimate with its interval", async () => {
    await door(s.api, "settle", { experiment_id: experiment, report: REPORTS.inconclusive });
    const page = await dev.get(`/experiments/${experiment}`);
    const c = comparison(REPORTS.inconclusive);
    assert.ok(page.text.includes(c.outcome), c.outcome);
    for (const reason of c.reasons) assert.ok(page.text.includes(reason), reason);
    for (const e of c.estimates) assert.ok(page.text.includes(`${e.name} ${e.paired} ${e.diff} ${e.interval} ${e.margin} ${e.verdict}`), e.name);
    assert.ok(!page.text.includes("Pending: B2 compares"));
    assert.match((await dev.get("/evaluations")).text, new RegExp(`${experiment} \\S+ finished cancelled ${c.outcome.replace(/[.:]/g, "\\$&")}`));
  });

  await launching("E2E-E05 a viewer, another provider and a consumer-only account see and launch nothing that is not theirs", async () => {
    const viewer = await as("viewer");
    const seen = await viewer.get("/evaluations");
    assert.ok(seen.text.includes(experiment) && forms(seen.html).length === 1, "a viewer reads; its only form is sign-out");
    const devPage = await dev.get("/evaluations");
    const launch = form(devPage.html, /Queue baseline and candidate runs/);
    assert.equal((await viewer.submit("/evaluations", launch, { ...LAUNCH, candidate_serving_ref: w.servings[1] })).location, "/evaluations?refused=denied");
    assert.ok((await viewer.get("/evaluations?refused=denied")).text.includes(REFUSAL_COPY.denied));
    const other = await as("other_dev");
    const theirs = await other.get("/evaluations");
    assert.ok(theirs.text.includes("No experiments yet.") && !theirs.text.includes(experiment));
    assert.ok((await other.get(`/experiments/${experiment}`)).text.includes(REFUSAL_COPY.not_found));
    assert.equal((await other.submit("/evaluations", launch, { ...LAUNCH, candidate_serving_ref: w.servings[1] })).location, "/evaluations?refused=invalid", "A's refs are not B's to launch");
    assert.ok((await (await as("consumer")).get("/evaluations")).text.includes(ACCESS_COPY.denied));
    const mine = await dev.get("/evaluations");
    assert.equal([...mine.text.matchAll(/lab:run:/g)].length, 2, "still only the one experiment's two runs");
  });
  record("evaluate", { cell: "E6L j10", composed: w.composed, stand_ins: w.stand_ins });
});
