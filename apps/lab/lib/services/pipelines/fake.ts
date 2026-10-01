// P4: an in-memory stand-in for the LAB_PIPELINES route over P1/P3 (the WR-P4-1 shape), for tests/p and
// the labelled dev preview. It enforces what P1/P3 do: provider scoping (another provider's record is
// not_found), the role (denied), write-once ids (a replay is the stored record, another body a conflict),
// train-only exports, the manual bundle reserving nothing, an ambiguous submit only ever looked up, and
// checkpoint rejection before any evaluation. It does not model reviewer identity (the route derives the
// user from the session). `dataset`, `artifact`, `evaluated`, `ambiguous`, `found`, `revoke` and `expire`
// are the other services' side; the Lab never calls them. Teacher batches (P2 behind the route) are a dry run
// until an administrator approves one within its budget: the batch is looked up before the role (R183), each
// chunk reserves its ceiling against the payer and is sent once, a lost answer stays ambiguous and is never
// resent; `teacherLive`, `teacherMode`, `payerBudgetUsd` and `teacherFailures` are the other side.
import { createHash, randomUUID } from "node:crypto";
import {
  holds, MANUAL, type Actor, type AdjudicateInput, type AssignInput, type Capability, type Checkpoint, type CheckpointInput,
  type Disagreement, type ExportInput, type ImportInput, type ImportReceipt, type Label, type LabelExport, type PipelinesPort,
  type PrepareInput, type Refusal, type Result, type ReviewInput, type TeacherBatch, type TeacherInput, type TrainingRun,
} from "./port.ts";
import { USD_RE as USD } from "../shapes.ts";

type Sample = { sampleId: string; split: "train" | "validation" | "holdout"; readable?: boolean };
type Owned<T> = T & { providerId: string };
const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const no = <T>(reason: Refusal): Result<T> => ({ ok: false, reason });
const sha = (v: unknown) => createHash("sha256").update(JSON.stringify(v)).digest("hex");
const METHODS: Record<string, Label["method"]> = { human: "imported", model: "synthetic" };
const ROW_KEYS = new Set(["sample_id", "method", "method_version", "label", "ground_truth", "annotator", "model", "prompt", "confidence", "spans"]);
const LIVE = ["submitted", "accepted"];
/** The teacher's worst case per sample in 1e-8 USD (a test rate, not a price); another model is unpriced. */
const TEACHER_RATES: Record<string, bigint> = { "claude-opus-5": BigInt(11_354_000) };
const E8 = BigInt(100_000_000);
const units = (usd: string) => BigInt(usd.replace(".", ""));
const usd = (n: bigint) => `${n / E8}.${String(n % E8).padStart(8, "0")}`;
/** A record as the route returns it: the fake's own bookkeeping fields dropped, a copy. */
const bare = <T>(row: object): T => {
  const { providerId, body, expired, bundled, dataset, ids, ...rest } = row as Record<string, unknown>;
  return (void [providerId, body, expired, bundled, dataset, ids], structuredClone(rest) as T);
};

export class FakePipelines implements PipelinesPort {
  readonly calls: unknown[][] = [];
  submissions = 0;
  lookups = 0;
  reservations = 0;
  evaluationsQueued = 0;
  private sets = new Map<string, { providerId: string; samples: Sample[] }>();
  private rows: Owned<Label & { dataset: string }>[] = [];
  private receipts: Owned<ImportReceipt & { body: string }>[] = [];
  private exported: Owned<LabelExport & { body: string; expired: boolean }>[] = [];
  private trainings: Owned<TrainingRun & { body: string; bundled: { train: string[]; dev: string[] } }>[] = [];
  private points: Owned<Checkpoint>[] = [];
  private objects = new Map<string, string>();
  private teachers: Owned<TeacherBatch & { body: string; ids: string[][] }>[] = [];
  teacherLive = true;
  teacherMode: "ok" | "lost" = "ok";
  payerBudgetUsd = "100.00000000";
  teacherSends = 0;
  private foundKeys = new Set<string>();

  private gate(actor: Actor, capability: Capability = "run_evaluation"): Refusal | null {
    return holds(actor.role, capability) ? null : "denied";
  }
  private set(actor: Actor, ref: string) {
    const d = this.sets.get(ref);
    return d?.providerId === actor.providerId ? d : undefined;
  }
  private mine<T>(actor: Actor, rows: Owned<object>[]): Result<T[]> {
    const refused = this.gate(actor);
    return refused ? no(refused) : ok(rows.filter((r) => r.providerId === actor.providerId).map((r) => bare<T>(r)));
  }
  private run(actor: Actor, id: string) {
    return this.trainings.find((r) => r.externalRunId === id && r.providerId === actor.providerId);
  }
  private label(actor: Actor, datasetRef: string, sampleId: string, fields: Pick<Label, "method" | "state" | "value" | "reviewerId">) {
    const row = { providerId: actor.providerId, dataset: datasetRef, annotationRef: `lab:annotation:${actor.providerId}:${randomUUID()}@sha256:${sha([datasetRef, sampleId, fields])}`, sampleId, groundTruth: fields.method === "human", ...fields };
    this.rows.push(row);
    return row;
  }

  async labels(actor: Actor, datasetRef: string): Promise<Result<Label[]>> {
    const refused = this.gate(actor);
    if (refused) return no(refused);
    if (!this.set(actor, datasetRef)) return no("not_found");
    return ok(this.rows.filter((r) => r.dataset === datasetRef).map((r) => bare<Label>(r)));
  }

  async disagreements(actor: Actor, datasetRef: string): Promise<Result<Disagreement[]>> {
    const labels = await this.labels(actor, datasetRef);
    if (!labels.ok) return labels;
    const live = new Map<string, Label[]>();
    for (const l of labels.value.filter((x) => LIVE.includes(x.state))) live.set(l.sampleId, [...(live.get(l.sampleId) ?? []), l]);
    return ok([...live].filter(([, ls]) => new Set(ls.map((l) => l.value)).size > 1)
      .map(([sampleId, ls]) => ({ sampleId, annotationRefs: ls.map((l) => l.annotationRef).sort() })));
  }

  async imports(actor: Actor) { return this.mine<ImportReceipt>(actor, this.receipts); }
  async exports(actor: Actor) { return this.mine<LabelExport>(actor, this.exported); }
  async runs(actor: Actor) { return this.mine<TrainingRun>(actor, this.trainings); }
  async checkpoints(actor: Actor) { return this.mine<Checkpoint>(actor, this.points); }

  async importLabels(actor: Actor, input: ImportInput): Promise<Result<ImportReceipt>> {
    this.calls.push(["importLabels", actor, input]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const set = this.set(actor, input.datasetRef);
    if (!set) return no("not_found");
    const body = JSON.stringify(input);
    const prior = this.receipts.find((r) => r.importId === input.importId && r.providerId === actor.providerId);
    if (prior) return prior.body === body ? ok(bare<ImportReceipt>(prior)) : no("conflict");
    const receipt: ImportReceipt = { importId: input.importId, datasetRef: input.datasetRef, accepted: 0, rejected: [] };
    input.rows.split("\n").filter((l) => l.trim() !== "").forEach((line, i) => {
      let row: Record<string, unknown>;
      try { row = JSON.parse(line); } catch { row = {}; }
      const sample = set.samples.find((s) => s.sampleId === row.sample_id);
      const reason = !sample ? "missing_evidence" : sample.readable === false ? "grant_not_current"
        : (row.ground_truth ?? false) !== false ? "forged_ground_truth"
        : Object.keys(row).some((k) => !ROW_KEYS.has(k)) || !(String(row.method) in METHODS) || !("label" in row) || typeof row.method_version !== "string" ? "bad_mapping" : null;
      if (reason) return void receipt.rejected.push({ row: i + 1, reason });
      this.label(actor, input.datasetRef, sample!.sampleId, { method: METHODS[String(row.method)], state: "submitted", value: JSON.stringify(row.label), reviewerId: null });
      receipt.accepted += 1;
    });
    this.receipts.push({ providerId: actor.providerId, body, ...receipt });
    return ok(receipt);
  }

  async assign(actor: Actor, input: AssignInput): Promise<Result<null>> {
    this.calls.push(["assign", actor, input]);
    const refused = this.gate(actor, "manage_members");
    if (refused) return no(refused);
    return this.set(actor, input.datasetRef)?.samples.some((s) => s.sampleId === input.sampleId) ? ok(null) : no("not_found");
  }

  async review(actor: Actor, input: ReviewInput): Promise<Result<null>> {
    this.calls.push(["review", actor, input]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const row = this.rows.find((r) => r.annotationRef === input.annotationRef && r.dataset === input.datasetRef && r.providerId === actor.providerId);
    if (!row) return no("not_found");
    if (row.state !== "submitted") return row.state === input.decision && input.correction === null ? ok(null) : no("conflict");
    row.state = input.decision;
    if (input.correction !== null) this.label(actor, input.datasetRef, row.sampleId, { method: "human", state: "accepted", value: input.correction, reviewerId: "session-user" });
    return ok(null);
  }

  async adjudicate(actor: Actor, input: AdjudicateInput): Promise<Result<null>> {
    this.calls.push(["adjudicate", actor, input]);
    const found = await this.disagreements(actor, input.datasetRef);
    if (!found.ok) return found;
    const disputed = found.value.find((d) => d.sampleId === input.sampleId);
    if (!disputed) return no("conflict");
    for (const r of this.rows.filter((x) => disputed.annotationRefs.includes(x.annotationRef))) r.state = r.state === "accepted" ? "superseded" : "rejected";
    this.label(actor, input.datasetRef, input.sampleId, { method: "human", state: "accepted", value: input.value, reviewerId: "session-user" });
    return ok(null);
  }

  async exportLabels(actor: Actor, input: ExportInput): Promise<Result<LabelExport>> {
    this.calls.push(["exportLabels", actor, input]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const set = this.set(actor, input.datasetRef);
    if (!set) return no("not_found");
    const body = JSON.stringify(input);
    const prior = this.exported.find((e) => e.exportId === input.exportId && e.providerId === actor.providerId);
    if (prior) return prior.body === body ? ok(bare<LabelExport>(prior)) : no("conflict");
    const out: LabelExport = { exportId: input.exportId, datasetRef: input.datasetRef, adapter: input.adapter, items: 0, expiresAt: new Date(Date.now() + input.ttlS * 1000).toISOString(), lineage: [], omitted: [] };
    for (const s of [...set.samples].sort((a, b) => a.sampleId.localeCompare(b.sampleId))) {
      const accepted = this.rows.filter((r) => r.dataset === input.datasetRef && r.sampleId === s.sampleId && r.state === "accepted");
      if (accepted.length === 0) continue;
      const values = new Set(accepted.map((r) => r.value));
      const reason = s.split !== "train" ? s.split : s.readable === false ? "grant_not_current" : values.size > 1 ? "disagreement" : null;
      if (reason) { out.omitted.push({ sampleId: s.sampleId, reason }); continue; }
      out.items += 1;
      out.lineage.push({ sampleId: s.sampleId, labelRefs: accepted.map((r) => r.annotationRef).sort(), methods: [...new Set(accepted.map((r) => r.method))].sort() });
    }
    this.exported.push({ providerId: actor.providerId, body, expired: false, ...out });
    return ok(out);
  }

  async bundle(actor: Actor, externalRunId: string): Promise<Result<string>> {
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const r = this.run(actor, externalRunId);
    if (!r) return no("not_found");
    return ok(JSON.stringify({ format: "infrx.training_bundle.1", external_run_ref: r.runRef, dataset_ref: r.datasetRef, config: r.config, ...r.bundled, export: r.export, holdout: r.holdout }));
  }

  async prepare(actor: Actor, input: PrepareInput): Promise<Result<TrainingRun>> {
    this.calls.push(["prepare", actor, input]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const set = this.set(actor, input.datasetRef);
    if (!set) return no("not_found");
    const body = JSON.stringify(input);
    const prior = this.run(actor, input.externalRunId);
    if (prior) return prior.body === body ? ok(bare<TrainingRun>(prior)) : no("conflict");
    const { objective, adaptation, baseModel } = input.config;
    if (!["sft", "preference"].includes(objective) || !["full", "lora"].includes(adaptation) || !baseModel || !USD.test(input.limitUsd)
      || !input.payerRef.startsWith(`lab:payer:${actor.providerId}:`)) return no("invalid");
    const exp = this.exported.find((e) => e.exportId === input.exportId && e.providerId === actor.providerId && input.exportFormat === "infrx.label_export.1");
    if (!exp || exp.datasetRef !== input.datasetRef) return no("not_found");
    if (exp.expired) return no("gone");
    const allowed = set.samples.filter((s) => s.readable !== false);
    const train = allowed.filter((s) => s.split === "train").map((s) => s.sampleId);
    const dev = allowed.filter((s) => s.split === "validation").map((s) => s.sampleId);
    if (train.length === 0 || exp.lineage.some((x) => ![...train, ...dev].includes(x.sampleId))) return no("denied");
    const holdout = set.samples.filter((s) => s.split === "holdout").map((s) => s.sampleId).sort();
    const r: (typeof this.trainings)[number] = {
      providerId: actor.providerId, body, bundled: { train, dev }, externalRunId: input.externalRunId,
      runRef: `lab:external_run:${actor.providerId}:${input.externalRunId}@sha256:${sha(body)}`, connector: MANUAL, state: "prepared",
      datasetRef: input.datasetRef, export: { format: input.exportFormat, exportId: input.exportId, sha256: sha(exp.lineage) }, config: { objective, adaptation, baseModel },
      train: train.length, dev: dev.length, omitted: set.samples.filter((s) => s.split !== "holdout" && s.readable === false).length,
      holdout: { size: holdout.length, sha256: sha(holdout) }, payerRef: input.payerRef, limitUsd: input.limitUsd, reservedUsd: "0.00000000",
      settled: false, costUsd: null, reason: null,
    };
    this.trainings.push(r);
    return ok(bare<TrainingRun>(r));
  }

  async submit(actor: Actor, externalRunId: string): Promise<Result<TrainingRun>> {
    this.calls.push(["submit", actor, externalRunId]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const r = this.run(actor, externalRunId);
    if (!r) return no("not_found");
    if (r.state === "ambiguous") {
      this.lookups += 1; // reconciled by lookup only: never submitted again
      if (this.foundKeys.has(externalRunId)) r.state = "submitted";
      return ok(bare<TrainingRun>(r));
    }
    if (r.state !== "prepared") return ok(bare<TrainingRun>(r));
    const readable = new Set(this.sets.get(r.datasetRef)!.samples.filter((s) => s.readable !== false).map((s) => s.sampleId));
    if ([...r.bundled.train, ...r.bundled.dev].some((id) => !readable.has(id))) return no("denied");
    if (r.connector !== MANUAL) this.reservations += 1;
    this.submissions += 1;
    r.state = "submitted";
    return ok(bare<TrainingRun>(r));
  }

  async finish(actor: Actor, externalRunId: string): Promise<Result<TrainingRun>> {
    this.calls.push(["finish", actor, externalRunId]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const r = this.run(actor, externalRunId);
    if (!r) return no("not_found");
    if (!["submitted", "completed"].includes(r.state)) return no("conflict");
    r.state = "completed";
    return ok(bare<TrainingRun>(r));
  }

  async cancel(actor: Actor, externalRunId: string): Promise<Result<TrainingRun>> {
    this.calls.push(["cancel", actor, externalRunId]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const r = this.run(actor, externalRunId);
    if (!r) return no("not_found");
    if (!["prepared", "submitted"].includes(r.state)) return no("conflict");
    r.state = "cancelled";
    return ok(bare<TrainingRun>(r));
  }

  async importCheckpoint(actor: Actor, input: CheckpointInput): Promise<Result<Checkpoint>> {
    this.calls.push(["importCheckpoint", actor, input]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const r = this.run(actor, input.externalRunId);
    if (!r) return no("not_found");
    if (!input.artifactKey.startsWith(`lab/${actor.providerId}/training/${input.externalRunId}/`)) return no("invalid");
    const prior = this.points.find((c) => c.checkpointId === input.checkpointId && c.providerId === actor.providerId);
    if (prior) return ok(bare<Checkpoint>(prior));
    const stored = this.objects.get(input.artifactKey);
    const reason = !["submitted", "completed"].includes(r.state) ? `run_${r.state}` : stored === undefined ? "missing_artifact" : stored !== input.artifactDigest ? "digest_mismatch" : null;
    if (!reason) this.evaluationsQueued += 1;
    const c: Owned<Checkpoint> = {
      providerId: actor.providerId, checkpointId: input.checkpointId, externalRunId: r.externalRunId, artifactDigest: input.artifactDigest,
      state: reason ? "rejected" : "validated", reason,
      evaluation: reason ? null : { runRef: `lab:run:${actor.providerId}:${randomUUID()}@sha256:${sha(input)}`, state: "queued", split: "holdout", holdoutSha256: r.holdout.sha256 },
      eligible: false,
    };
    this.points.push(c);
    return ok(bare<Checkpoint>(c));
  }

  async approve(actor: Actor, input: { externalRunId: string; checkpointId: string }): Promise<Result<Checkpoint>> {
    this.calls.push(["approve", actor, input]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const r = this.run(actor, input.externalRunId);
    if (!r) return no("not_found");
    const c = this.points.find((x) => x.checkpointId === input.checkpointId && x.externalRunId === r.externalRunId && x.providerId === actor.providerId);
    if (c?.evaluation?.state !== "succeeded") return no("conflict");
    c.eligible = true;
    return ok(bare<Checkpoint>(c));
  }

  async teacherBatches(actor: Actor) { return this.mine<TeacherBatch>(actor, this.teachers); }

  async planTeachers(actor: Actor, input: TeacherInput): Promise<Result<TeacherBatch>> {
    this.calls.push(["planTeachers", actor, input]);
    const refused = this.gate(actor);
    if (refused) return no(refused);
    const set = this.set(actor, input.datasetRef);
    if (!set || !input.payerRef.startsWith(`lab:payer:${actor.providerId}:`) || !USD.test(input.budgetUsd)
      || !Number.isInteger(input.chunkSize) || input.chunkSize < 1 || input.chunkSize > 200) return no("invalid");
    const body = JSON.stringify(input);
    const prior = this.teachers.find((b) => b.batchId === input.batchId && b.providerId === actor.providerId);
    if (prior) return prior.body === body ? ok(bare<TeacherBatch>(prior)) : no("conflict");
    const kept = set.samples.filter((x) => x.split !== "holdout").map((x) => x.sampleId).sort();
    const ids = Array.from({ length: Math.ceil(kept.length / input.chunkSize) }, (_, i) => kept.slice(i * input.chunkSize, (i + 1) * input.chunkSize));
    const rate = TEACHER_RATES[input.teacherModel];
    const ceiling = rate === undefined ? null : rate * BigInt(kept.length);
    const b: (typeof this.teachers)[number] = {
      providerId: actor.providerId, body, ids, ...input, requestedBy: "session-user", priceVersion: rate === undefined ? null : "test-rates-v1",
      ceilingUsd: ceiling === null ? null : usd(ceiling), withinBudget: ceiling !== null && ceiling <= units(input.budgetUsd),
      holdout: set.samples.length - kept.length, notPermitted: set.samples.filter((x) => x.split !== "holdout" && x.readable === false).length, approval: null,
      chunks: ids.map((chunk, i) => ({ runId: `${input.batchId}:${i}`, samples: chunk.length, ceilingUsd: rate === undefined ? null : usd(rate * BigInt(chunk.length)),
        state: "unreserved", reservedUsd: null, costUsd: null, sent: 0, failures: [] })),
    };
    this.teachers.push(b);
    return ok(bare<TeacherBatch>(b));
  }

  async approveTeachers(actor: Actor, batchId: string): Promise<Result<TeacherBatch>> {
    this.calls.push(["approveTeachers", actor, batchId]);
    const b = this.teachers.find((x) => x.batchId === batchId && x.providerId === actor.providerId);
    if (!b) return no("not_found");
    const refused = this.gate(actor, "manage_members");
    if (refused) return no(refused);
    if (!b.withinBudget) return no("conflict");
    if (!this.teacherLive) return no("unavailable");
    b.approval ??= { approvedBy: "session-user", approvedAt: "2026-09-28T10:00:00Z" };
    const readable = new Set(this.sets.get(b.datasetRef)!.samples.filter((x) => x.readable !== false).map((x) => x.sampleId));
    for (const [i, c] of b.chunks.entries()) {
      if (c.state !== "unreserved") continue; // a resume: reserved chunks are never sent again
      const held = this.teachers.flatMap((x) => x.chunks).filter((x) => x.reservedUsd !== null).reduce((n, x) => n + units(x.reservedUsd!), BigInt(0));
      if (held + units(c.ceilingUsd!) > units(this.payerBudgetUsd)) break; // the budget stops the batch
      this.teacherSends += 1;
      Object.assign(c, { reservedUsd: c.ceilingUsd, sent: b.ids[i].filter((id) => readable.has(id)).length, state: this.teacherMode === "lost" ? "ambiguous" : "submitted" });
    }
    return ok(bare<TeacherBatch>(b));
  }

  // --- the other services' side ------------------------------------------------------------------
  dataset(providerId: string, ref: string, samples: Sample[]): void { this.sets.set(ref, { providerId, samples }); }
  artifact(key: string, digest: string): void { this.objects.set(key, digest); }
  evaluated(checkpointId: string, state: "running" | "succeeded" | "failed"): void {
    const c = this.points.find((x) => x.checkpointId === checkpointId);
    if (c?.evaluation) c.evaluation.state = state;
  }
  ambiguous(externalRunId: string): void { this.trainings.find((r) => r.externalRunId === externalRunId)!.state = "ambiguous"; }
  found(externalRunId: string): void { this.foundKeys.add(externalRunId); }
  revoke(datasetRef: string, sampleId: string): void {
    this.sets.get(datasetRef)!.samples.find((s) => s.sampleId === sampleId)!.readable = false;
  }
  teacherFailures(runId: string, failures: { sampleId: string; reason: string }[]): void {
    this.teachers.flatMap((b) => b.chunks).find((c) => c.runId === runId)!.failures.push(...failures);
  }
  expire(exportId: string): void { this.exported.find((e) => e.exportId === exportId)!.expired = true; }
}
