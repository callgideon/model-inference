// LAB-E2E improve = E7L i08's provider-UI half (WR-C4-UI): the Lab's annotations and training pages,
// served by the built Lab app and signed in through its own form, over lab-api's /lab/v1/pipelines
// (LAB_PIPELINES as the gateway composes it: D8's label log and run ledger, D7, L2) on key l4
// (backend.py): labels imported, assigned and reviewed, a train-only export, the manual bundle submitted
// and finished, a checkpoint returned, and never an eligible candidate without its held-out evaluation.
// Skipped unless LAB_E2E_REAL=1 (Docker, l4):
//   cd apps/lab && LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test tests/e2e/improve/stack.test.ts
import assert from "node:assert/strict";
import test from "node:test";
import { ACCESS_COPY } from "../../../lib/auth/access.ts";
import { REFUSAL_COPY } from "../../../lib/services/pipelines/view.ts";
import { door, form, forms, record, SKIP, stack, type Browser } from "../harness.ts";

type World = {
  A: string; B: string; dataset: string; rubric: string; payer: string; train: string[]; holdout: string[]; validation: string[];
  users: Record<string, string>; composed: Record<string, boolean>; stand_ins: string[];
};

test("E2E-I i08 the pipeline surface drives labels to a checkpoint through the provider UI", { skip: SKIP }, async (t) => {
  const s = await stack<World>("improve");
  t.after(s.stop);
  const w = s.world;
  const as = async (who: string): Promise<Browser> => {
    const b = s.browser();
    assert.equal((await b.signIn(`${who}@lab.e2e`)).location, "/", `${who} signs in`);
    return b;
  };
  const [dev, admin] = [await as("dev"), await as("admin")];
  const labels = `/annotations?dataset=${encodeURIComponent(w.dataset)}`;
  const human = (sample: string, answer: string, extra: object = {}) => JSON.stringify({ sample_id: sample, method: "human", method_version: "h1", label: { answer }, ...extra });
  const labelled = [...w.train, w.holdout[0]];
  let exportId = "";

  await t.test("E2E-I01 as the gateway composes LAB_PIPELINES today: the annotations page reads, the training page fails closed", async () => {
    await door(s.api, "composition", { as: "gateway" });
    assert.ok((await dev.get("/training")).text.includes(REFUSAL_COPY.unavailable), "no run listing (WR-LAB2-4): nothing shown");
    const page = await dev.get(labels);
    assert.ok(page.text.includes("No labels for this dataset version yet.") && page.text.includes("No label imports yet."));
  });

  await t.test("E2E-I02 labels imported through the page's form: accepted rows listed as not ground truth, rejected rows with their reason", async () => {
    const rows = [...labelled.map((x, i) => human(x, `a${i}`)), human(w.train[0], "forged", { ground_truth: true }), human("not-a-sample", "x")].join("\n");
    const landed = await dev.submit(labels, form((await dev.get(labels)).html, /Import labels/), { rubricRef: w.rubric, rows });
    assert.equal(landed.location, labels, "the action returns to the records");
    const page = await dev.get(labels);
    assert.ok(page.text.includes(`${w.dataset}: 5 accepted · 2 rejected`), "the import receipt");
    assert.ok(page.text.includes("Row 6: an import cannot claim ground truth") && page.text.includes("Row 7: sample is not in this dataset"));
    for (const x of labelled) assert.match(page.text, new RegExp(`${x} Imported \\(external human pipeline\\) not ground truth submitted`), x);
  });

  await t.test("E2E-I03 an administrator assigns the reviewer, the developer accepts each label: an accepted import is still not ground truth", async () => {
    for (const x of labelled) {
      const assigned = await admin.submit(labels, form((await admin.get(labels)).html, /Assign a reviewer/), { sampleId: x, reviewerId: w.users.dev, rubricRef: w.rubric });
      assert.equal(assigned.location, labels, `assign ${x}`);
    }
    for (const x of labelled) {
      const open = forms((await dev.get(labels)).html).filter((f) => /Review$/.test(f.text));
      assert.equal(open.length, labelled.length - labelled.indexOf(x), "one review form per submitted label");
      assert.equal((await dev.submit(labels, open[0], { rubricRef: w.rubric })).location, labels);
    }
    const page = await dev.get(labels);
    for (const x of labelled) assert.match(page.text, new RegExp(`${x} Imported \\(external human pipeline\\) not ground truth accepted`), x);
    assert.equal(forms(page.html).filter((f) => /Review$/.test(f.text)).length, 0, "nothing left to review");
  });

  await t.test("E2E-I04 a train-only export through the page's form: train samples in its lineage, the held-out label omitted with its reason", async () => {
    assert.equal((await dev.submit(labels, form((await dev.get(labels)).html, /Export training labels/))).location, labels);
    const page = await dev.get(labels);
    const hit = new RegExp(`([0-9a-f-]{36}) · sft\\.1 · ${w.train.length} examples · expires`).exec(page.text);
    assert.ok(hit, "the export is listed from the records");
    exportId = hit[1];
    for (const x of w.train) assert.match(page.text, new RegExp(`${x} ← lab:annotation:\\S+ \\(imported\\)`), x);
    assert.match(page.text, new RegExp(`Omitted ${w.holdout[0]}: \\S+`), "the held-out label is omitted");
  });

  await t.test("E2E-I05 the manual bundle: prepared, submitted and finished from the training page; a returned checkpoint is never eligible without its held-out evaluation", async () => {
    await door(s.api, "composition", { as: "journey" });
    const page = await dev.get("/training");
    const prepared = await dev.submit("/training", form(page.html, /Prepare a training bundle/), {
      datasetRef: w.dataset, exportId, baseModel: "marlin-2b", payerRef: w.payer, limitUsd: "25.00000000",
    });
    assert.equal(prepared.location, "/training");
    let runs = await dev.get("/training");
    const run = /<input type="hidden" name="externalRunId" value="([0-9a-f-]{36})"/.exec(runs.html)![1];
    assert.ok(runs.text.includes("Prepared: the bundle is ready; nothing submitted."));
    assert.match(runs.text, new RegExp(`${w.train.length} train, \\d+ dev, \\d+ omitted · export ${exportId}`));
    const bundle = JSON.parse(/Bundle (\{.*\}) Checkpoints/.exec(runs.text)![1]);
    assert.deepEqual([bundle.train, bundle.holdout.size], [w.train, w.holdout.length], "the bundle trains on the train export and pins the holdout");
    assert.ok(w.holdout.every((x) => !runs.text.includes(x)), "no held-out sample is a training input");
    assert.match(runs.text, new RegExp(`${w.holdout.length} held-out samples, pinned ${bundle.holdout.sha256}`));
    assert.ok(runs.text.includes(`limit 25.00000000 USD · reserved 0.00000000 USD · payer ${w.payer}`));
    for (const [button, state] of [[/^Submit bundle$/, "Training on your own compute; mark it finished when it is."], [/^Mark training finished$/, "Training finished."]] as const) {
      assert.equal((await dev.submit("/training", form(runs.html, button))).location, "/training", String(button));
      runs = await dev.get("/training");
      assert.ok(runs.text.includes(state), state);
    }
    assert.ok(runs.text.includes("Nothing reserved or charged: the manual bundle trains on your own compute."));
    assert.deepEqual(forms(runs.html).filter((f) => /Submit bundle|Mark training finished|Cancel run/.test(f.text)), [], "a completed run offers nothing");
    const { key, digest } = await door(s.api, "artifact", { external_run_id: run, name: "checkpoints/1.json" });
    const imported = await dev.submit("/training", form(runs.html, /Import a checkpoint/), { artifactKey: String(key), artifactDigest: String(digest) });
    const after = await dev.get("/training");
    const lineage = `[0-9a-f-]{36} lab:external_run:${w.A}:${run}@sha256:[0-9a-f]{64} → ${w.dataset.replace(/[.$]/g, "\\$&")} · export ${exportId} · sft · lora · base marlin-2b · holdout ${bundle.holdout.sha256} ${digest}`;
    if (w.composed.suites) {
      assert.equal(imported.location, "/training");
      assert.match(after.text, new RegExp(`${lineage} Held-out evaluation (queued|running)\\.`));
    } else {
      // WR-B3-3 is not composed on this tip: D7 keeps the validated receipt, P3's evaluation port freezes
      // nothing (a typed 503, the fixed copy), and the page says why the checkpoint is not eligible
      assert.equal(imported.location, "/training?refused=unavailable");
      assert.ok((await dev.get(imported.location!)).text.includes(REFUSAL_COPY.unavailable));
      assert.match(after.text, new RegExp(`${lineage} Not evaluated on this run's pinned holdout: not eligible\\.`));
    }
    assert.ok(!/Eligible candidate/.test(after.text) && forms(after.html).every((f) => !/Approve as a candidate/.test(f.text)), "no approval without a succeeded held-out evaluation");
  });

  await t.test("E2E-I06 a viewer, another provider and a consumer-only account neither see nor write this provider's labels", async () => {
    const viewer = await as("viewer");
    const seen = await viewer.get(labels);
    assert.ok(seen.text.includes(REFUSAL_COPY.denied) && !seen.text.includes(w.train[0]), "a viewer's label read is refused, and the page says so");
    assert.ok(forms(seen.html).every((f) => !/Import labels|Export training labels|Review$|Assign a reviewer/.test(f.text)), "and offers no write");
    const write = form((await dev.get(labels)).html, /Import labels/);
    assert.equal((await viewer.submit(labels, write, { rubricRef: w.rubric, rows: human(w.train[0], "v") })).location, `${labels}&refused=denied`);
    const other = await as("other_dev");
    const theirs = await other.get(labels);
    assert.ok(!theirs.text.includes(w.train[0]) && !theirs.text.includes("Import labels"), "A's dataset is not a dataset of B's");
    assert.ok((await other.get("/training")).text.includes("No training runs yet."));
    assert.ok((await (await as("consumer")).get(labels)).text.includes(ACCESS_COPY.denied));
  });
  record("improve", { cell: "E7L i08 (UI)", composed: w.composed, stand_ins: w.stand_ins });
});
