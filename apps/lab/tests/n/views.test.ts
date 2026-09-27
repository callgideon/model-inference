// N4 CONSOLE-FLOWS / DATA-SPLIT: what the datasets pages say. Success only for a published record;
// restrictions and leaks are always explained.
import assert from "node:assert/strict";
import test from "node:test";
import type { ImportJob, VersionStatus } from "../../lib/services/datasets/port.ts";
import { importView, leakageWarnings, rejectedRowsJsonl, restrictedCopy, RESTRICTED_COPY, splitSummary } from "../../lib/services/datasets/views.ts";

const report = { accepted: 3, rejected: [{ line: 4, reason: "bad_split", detail: "split 'test'" }], datasetRef: null, sourceRef: null };
const job = (state: ImportJob["state"], extra: Partial<ImportJob> = {}): ImportJob => ({ importId: "i", state, report, error: null, ...extra });

test("N4-V01 only a published import with its dataset reads as success; the rest say how to resume", () => {
  assert.equal(importView(job("published", { report: { ...report, datasetRef: "lab:dataset:d" } })).tone, "success");
  assert.equal(importView(job("published")).tone, "error");
  assert.deepEqual([importView(job("running")).tone, importView(job("running")).poll], ["progress", true]);
  for (const state of ["rejected", "failed"] as const) {
    const view = importView(job(state));
    assert.equal(view.tone, "error");
    assert.equal(view.poll, false);
    assert.match(view.detail, /same import id/);
  }
  assert.match(importView(job("rejected")).detail, /1 row\(s\) were rejected and 3 accepted/);
});

test("N4-V02 the rejected-rows download is line, reason and detail only", () => {
  const withRow = job("rejected", { report: { ...report, rejected: [{ line: 4, reason: "bad_split", detail: "d", row: { secret: 1 } } as never] } });
  assert.equal(rejectedRowsJsonl(withRow), '{"line":4,"reason":"bad_split","detail":"d"}\n');
});

test("N4-V03 the split summary counts samples and restrictions per split", () => {
  const s = (split: "train" | "validation" | "holdout", restricted: string | null) => ({ sampleId: `${split}${restricted}`, split, sourceRef: "r", grantRef: "g", trace: null, restricted });
  const status: VersionStatus = { datasetRef: "d", parentRefs: [], samples: [s("train", null), s("train", "deleted"), s("holdout", null)] };
  assert.deepEqual(splitSummary(status), [
    { split: "train", samples: 2, restricted: 1 }, { split: "validation", samples: 0, restricted: 0 }, { split: "holdout", samples: 1, restricted: 0 },
  ]);
});

test("N4-V04 every restriction is explained; an unknown reason is still a restriction", () => {
  for (const reason of ["grant_not_current", "deleted", "content_expired", "grant_revoked"]) assert.equal(restrictedCopy(reason), RESTRICTED_COPY[reason]);
  assert.match(restrictedCopy("quarantined"), /Restricted \(quarantined\)\. The sample is excluded/);
  assert.match(RESTRICTED_COPY.deleted, /purged/);
});

test("N4-V05 leakage warnings name leaks, near-duplicate families and holdout relatives", () => {
  const warnings = leakageWarnings({
    leaks: [{ samples: ["a", "b"], splits: ["holdout", "train"] }],
    derived: { datasetRef: "d", splitDigest: "s", review: [["x", "y", "z"]], omitted: [{ sampleId: "h1", reason: "related_to_holdout" }, { sampleId: "g", reason: "duplicate" }] },
  });
  assert.deepEqual(warnings, [
    "Leak: 2 related samples sit in holdout and train. The version was refused.",
    "Review: 3 samples from different groups share a clip or near-identical text and were kept in one split.",
    "Omitted h1: related to a frozen holdout sample.",
  ]);
});
