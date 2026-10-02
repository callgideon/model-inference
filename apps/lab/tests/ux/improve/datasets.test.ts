// UX-06 (UX-T10): the dataset workflow's decisions as pure functions - exact split percentages, the
// preview that only counts for the file and mapping it checked, the import step a provider is on, and
// the import spec template drawn from the real schema (infrx.dataset_import.1). Each case names what
// broken behaviour it catches.
import assert from "node:assert/strict";
import test from "node:test";
import { bpPercent, importStep, importTemplate, percentToBp, previewKey, splitPlan } from "../../../lib/services/datasets/views.ts";

test("UX06-V01 a percentage with at most two decimals is exact integer basis points; anything else is refused", () => {
  for (const [text, bp] of [["80", 8000], ["12.5", 1250], ["0.25", 25], ["33.33", 3333], ["100", 10_000], ["0", 0], [" 7.05 ", 705], ["0.1", 10]] as const) {
    assert.equal(percentToBp(text), bp, text);
  }
  // float arithmetic would turn 0.29 into 28.999...; a third decimal, a sign, a comma or >100% is not a split
  assert.equal(percentToBp("0.29"), 29);
  for (const text of ["", "80.255", "-1", "100.01", "1e2", "80,5", "abc", ".5", "5."]) assert.equal(percentToBp(text), null, text);
  assert.deepEqual([bpPercent(8025), bpPercent(5), bpPercent(10_000), bpPercent(0)], ["80.25%", "0.05%", "100.00%", "0.00%"]);
});

test("UX06-V02 the split plan shows all three shares, holdout the remainder, and refuses more than 100% or a malformed share", () => {
  assert.deepEqual(splitPlan("80", "10"), { ok: true, trainBp: 8000, validationBp: 1000, holdoutBp: 1000 });
  assert.deepEqual(splitPlan("70.5", "29.5"), { ok: true, trainBp: 7050, validationBp: 2950, holdoutBp: 0 });
  const over = splitPlan("90", "10.01");
  assert.equal(over.ok, false);
  assert.match(!over.ok ? over.message : "", /more than 100%/);
  const bad = splitPlan("80.123", "10");
  assert.equal(bad.ok, false);
  assert.match(!bad.ok ? bad.message : "", /two decimals/);
});

const FILE = { name: "rows.jsonl", size: 120, lastModified: 1 };

test("UX06-V03 a preview counts only for the exact mapping and file it checked: editing either one invalidates it", () => {
  const checked = previewKey('{"a":1}', FILE);
  assert.equal(previewKey('{"a":1}', { ...FILE }), checked, "the same mapping and file");
  assert.notEqual(previewKey('{"a":2}', FILE), checked, "an edited mapping");
  assert.notEqual(previewKey('{"a":1}', { ...FILE, size: 121 }), checked, "a changed file");
  assert.notEqual(previewKey('{"a":1}', { ...FILE, name: "other.jsonl" }), checked, "another file");
  assert.notEqual(previewKey('{"a":1}', { ...FILE, lastModified: 2 }), checked, "the file saved again");
  assert.notEqual(previewKey('{"a":1}', null), checked, "no file");
});

test("UX06-V04 the import step: a file, then a mapping, then a current preview; only then can it be imported", () => {
  const spec = '{"format":"infrx.dataset_import.1"}';
  assert.equal(importStep({ file: null, spec, previewed: null }), "source");
  assert.equal(importStep({ file: FILE, spec: "  ", previewed: null }), "mapping");
  assert.equal(importStep({ file: FILE, spec, previewed: null }), "validate");
  assert.equal(importStep({ file: FILE, spec, previewed: previewKey(spec, FILE) }), "import");
  assert.equal(importStep({ file: FILE, spec: spec + " ", previewed: previewKey(spec, FILE) }), "validate", "an edit after the preview goes back to validate");
  assert.equal(importStep({ file: { ...FILE, size: 9 }, spec, previewed: previewKey(spec, FILE) }), "validate", "a new file after the preview goes back to validate");
});

test("UX06-V05 the spec template is the real schema with this workspace's ids; rights, licence and mapping are left for the provider", () => {
  const ids = { providerId: "11111111-1111-4111-8111-111111111111", importId: "22222222-2222-4222-8222-222222222222", datasetId: "33333333-3333-4333-8333-333333333333", createdAt: "2026-10-02T10:00:00.000Z" };
  const spec = JSON.parse(importTemplate(ids));
  assert.deepEqual(Object.keys(spec), ["format", "provider_org_id", "import_id", "dataset_id", "version", "created_at", "modality", "ownership", "license", "use_restrictions", "grant_ref", "annotation", "fields"]);
  assert.equal(spec.format, "infrx.dataset_import.1");
  assert.deepEqual([spec.provider_org_id, spec.import_id, spec.dataset_id, spec.created_at], [ids.providerId, ids.importId, ids.datasetId, ids.createdAt]);
  assert.equal(spec.ownership, "provider_owned");
  // nothing is authorised or labelled on the provider's behalf: the backend refuses these empty values until filled
  assert.deepEqual([spec.license, spec.grant_ref, spec.annotation.method_version, spec.fields.content], ["", "", "", ""]);
  assert.deepEqual(spec.use_restrictions, []);
});
