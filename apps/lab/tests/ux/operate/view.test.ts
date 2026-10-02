// UX-03: the operate view models (lib/services/control/view.ts), pure: every label, tone, count and
// stage comes from the records, a failed read is never a zero or an empty list, and nothing derives a
// readiness or publication claim the records do not prove.
import assert from "node:assert/strict";
import test from "node:test";
import type { Aggregate, Deployment, Model, Proposal, Result } from "../../../lib/services/control/port.ts";
import * as v from "../../../lib/services/control/view.ts";

const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const down = { ok: false as const, reason: "unavailable" as const };
const model: Model = { modelId: "synthetic/alpha-2b", revisionLabel: "r1", artifactDigest: `sha256:${"a".repeat(64)}`, schemaVersion: "chat.v1", runtime: "vllm@sha256:bb", registeredAt: "2026-09-30T10:00:00Z" };
const dep = (over: Partial<Deployment> = {}): Deployment => ({
  deploymentRevisionId: "d-1", modelId: "synthetic/alpha-2b", servingVersionId: "sv-1", revisionLabel: "r1", runtime: "vllm@sha256:bb",
  schemaVersion: "chat.v1", rateCardVersion: null, environment: "dev", visibility: "private", state: "active", smoke: "none",
  createdAt: "2026-09-30T10:00:00Z", ...over,
});
const proposal = (over: Partial<Proposal> = {}): Proposal => ({
  proposalId: "p-1", kind: "publish", deploymentRevisionId: "d-1", state: "proposed", proposedAt: "2026-09-30T11:00:00Z", decidedAt: null, ...over,
});
const agg = (over: Partial<Aggregate> = {}): Aggregate => ({
  deploymentRevisionId: "d-1", windowStart: "2026-09-30T09:00:00Z", windowEnd: "2026-09-30T10:00:00Z", requests: 40, errors: 2, p95LatencyMs: 900, ...over,
});

test("OP-V01 the setup stages come from the records: a model, never a verified deployment from a record, a publication request", () => {
  const states = (m: Result<Model[]>, p: Result<Proposal[]>) => v.setupStages(m, p).map((s) => s.state);
  assert.deepEqual(states(ok([]), ok([])), ["todo", "todo", "todo"]);
  assert.deepEqual(states(ok([model]), ok([])), ["done", "todo", "todo"]);
  assert.deepEqual(states(ok([model]), ok([proposal()])), ["done", "todo", "done"]);
  assert.deepEqual(states(ok([model]), ok([proposal({ kind: "rollback" })])), ["done", "todo", "todo"], "only a publish request counts");
  assert.deepEqual(states(down, down), ["unknown", "todo", "unknown"], "a failed read is unknown, never not-started");
  const [, verify] = v.setupStages(ok([model]), ok([proposal({ state: "approved" })]));
  assert.match(verify.detail, /not available in this view/i);
  for (const s of v.setupStages(ok([model]), ok([proposal()]))) assert.ok(v.STAGE_BADGE[s.state].label.length > 0);
  assert.equal(v.STAGE_BADGE.unknown.tone, "warning");
  assert.notEqual(v.STAGE_BADGE.todo.tone, "success");
});

test("OP-V02 counts are the records' own, and a failed read is not available, never zero", () => {
  const counts = v.recordCounts(ok([dep(), dep({ deploymentRevisionId: "d-2", environment: "prod", visibility: "public", rateCardVersion: "rc-1" }), dep({ deploymentRevisionId: "d-3", state: "retired" })]), ok([proposal(), proposal({ proposalId: "p-2", state: "rejected" })]));
  assert.deepEqual(counts.map((c) => [c.label, c.value]), [["Registered deployment records", "2"], ["Public records", "1"], ["Pending publication requests", "1"]]);
  assert.deepEqual(v.recordCounts(down, ok([])).map((c) => c.value), [null, null, "0"]);
  assert.deepEqual(v.recordCounts(ok([]), down).map((c) => c.value), ["0", "0", null]);
  for (const c of counts) assert.match(c.href, /^\/deployments/);
});

test("OP-V03 measured traffic: no samples says so, a missing p95 is not available, and the evidence time is the window's", () => {
  const [busy, idle, blind] = v.trafficRows([agg(), agg({ deploymentRevisionId: "d-2", requests: 0, errors: 0, p95LatencyMs: null }), agg({ deploymentRevisionId: "d-3", p95LatencyMs: null })]);
  assert.deepEqual([busy.measured, busy.errorRate, busy.p95], [true, "5%", "900 ms"]);
  assert.equal(idle.measured, false);
  assert.equal(blind.p95, "Not available");
  assert.equal(v.observedThrough([agg(), agg({ windowEnd: "2026-09-30T12:00:00Z" })]), "2026-09-30T12:00:00Z");
  assert.equal(v.observedThrough([]), null);
});

test("OP-V04 a revision registration is checked field by field with the existing rules, and every outcome keeps the record or says it is unconfirmed", () => {
  const good = { name: "alpha-2b", artifactDigest: `sha256:${"a".repeat(64)}`, schemaVersion: "chat.v1", runtime: "vllm@sha256:bb" };
  assert.deepEqual(v.registrationErrors(good), {});
  const bad = v.registrationErrors({ name: "Alpha 2B", artifactDigest: "sha256:abc", schemaVersion: "", runtime: "has space" });
  assert.deepEqual(Object.keys(bad).sort(), ["artifactDigest", "name", "runtime", "schemaVersion"]);
  assert.match(bad.artifactDigest!, /sha256:/);
  assert.deepEqual(Object.keys(v.registrationErrors({ ...good, name: "a".repeat(64) })), ["name"], "63 characters at most");
  assert.deepEqual(v.registrationErrors({ ...good, name: "a".repeat(63) }), {});
  const record = dep();
  assert.deepEqual(v.registrationOutcome(ok(record)), { kind: "registered", deployment: record });
  const unsure = v.registrationOutcome(down);
  assert.equal(unsure.kind, "uncertain");
  assert.match(unsure.kind === "uncertain" ? unsure.message : "", /Check Models before trying again/);
  const refused = v.registrationOutcome({ ok: false, reason: "not_found" });
  assert.deepEqual(refused, { kind: "refused", message: v.REFUSAL_COPY.not_found });
  assert.deepEqual(v.importedNames([model, { ...model, revisionLabel: "r2" }, { ...model, modelId: "synthetic/beta-1b" }]), ["alpha-2b", "beta-1b"]);
  const [row] = v.modelRows([model]);
  assert.deepEqual([row.name, row.modelId, row.revision], ["alpha-2b", "synthetic/alpha-2b", "r1"]);
});

test("OP-V05 a deployment record is registered, not healthy; a recorded smoke never verifies an engine; publication and the operator's decision are separate facts", () => {
  assert.deepEqual(v.RECORD_STATE.active, { tone: "info", label: "Registered · active record" });
  assert.deepEqual(v.RECORD_STATE.retired, { tone: "neutral", label: "Retired" });
  const stages = (d: Deployment, p: Proposal[] | null) => v.readiness(d, p).map((c) => [c.stage, c.status]);
  const passed = dep({ smoke: "passed" });
  assert.deepEqual(stages(passed, []), [
    ["Record registered", "Registered"],
    ["Engine evidence", "Not verified here"],
    ["Private smoke on this engine and revision", "Not verified here"],
    ["Publication requested", "Not requested"],
    ["Operator decision", "No decision"],
  ]);
  for (const c of v.readiness(passed, [proposal({ state: "approved", decidedAt: "t" })])) {
    assert.doesNotMatch(c.status, /ready|healthy|live|published/i);
    if (c.stage !== "Operator decision") assert.notEqual(c.tone, "success");
  }
  assert.deepEqual(stages(passed, [proposal()]).slice(3), [["Publication requested", "Requested 2026-09-30T11:00:00Z"], ["Operator decision", "Awaiting operator decision"]]);
  assert.deepEqual(stages(passed, [proposal({ state: "rejected", decidedAt: "t" })])[4], ["Operator decision", "Rejected"]);
  assert.deepEqual(stages(passed, [proposal({ deploymentRevisionId: "other" })]).slice(3), [["Publication requested", "Not requested"], ["Operator decision", "No decision"]]);
  assert.deepEqual(stages(passed, null).slice(3).map(([, s]) => s), ["Couldn't check", "Couldn't check"]);
  assert.deepEqual(stages(dep({ state: "retired" }), [])[0], ["Record registered", "Retired"]);
  assert.match(v.recordedSmoke(passed), /^Recorded smoke result: passed\. .*does not verify/);
  assert.match(v.recordedSmoke(dep()), /No smoke result is recorded/);
});

test("OP-V06 a dev smoke is never offered until readiness checks exist; publication only to an administrator after a recorded pass", () => {
  const rows = (role: "viewer" | "developer" | "administrator", d: Deployment, p: Proposal[] = []) => v.operateActions(v.deploymentRows(role, [d], p)[0]);
  assert.deepEqual(rows("developer", dep()), []);
  assert.deepEqual(rows("developer", dep({ smoke: "passed" })), []);
  assert.deepEqual(rows("administrator", dep({ smoke: "passed" })), ["publish"]);
  assert.deepEqual(rows("administrator", dep({ smoke: "passed" }), [proposal()]), []);
  assert.deepEqual(rows("viewer", dep({ smoke: "passed" })), []);
});
