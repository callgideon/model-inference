// B4: an in-memory stand-in for LAB_EVALS (the WR-B4-1 shape), for tests/b and the labelled dev
// preview. It enforces what the backends must: provider scoping (another provider's record is
// not_found, before the role is looked at), run_evaluation for every write, refs the provider was
// offered (F3 refuses a foreign or mutable one), one launch or subscription per form id (the same body
// again is the same record, another body a conflict), a subscription's CREDIT-only limits (B3) and a
// cancel only while a run can still stop. `progress`, `settle` and `decide` are the workers' side
// (B1's runner, B2's compare, B3's handler): the Lab never calls them.
import { randomUUID } from "node:crypto";
import { holds, type Actor, type Catalog, type Decision, type EvaluationPort, type Experiment, type Launch, type Report, type Result, type Run, type Subscription, type SubscriptionRequest } from "./port.ts";

type Owned<T> = { providerId: string; row: T; body?: string };
const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const no = <T>(reason: "denied" | "not_found" | "invalid" | "conflict"): Result<T> => ({ ok: false, reason });
const EMPTY: Catalog = { datasets: [], harnesses: [], servings: [], evaluators: [] };
const copy = <T>(value: T): T => structuredClone(value);
const exact = (value: string) => BigInt(value.replace(".", "")); // both 8-decimal (F3), so exact

export class FakeEvaluation implements EvaluationPort {
  readonly calls: unknown[][] = [];
  private offered = new Map<string, Catalog>();
  private runRows: Owned<Run>[] = [];
  private experimentRows: Owned<Experiment>[] = [];
  private subscriptionRows: Owned<Subscription>[] = [];

  private mine<T>(actor: Actor, rows: Owned<T>[]): Result<T[]> {
    return ok(rows.filter((r) => r.providerId === actor.providerId).map((r) => copy(r.row)));
  }
  async catalog(actor: Actor) { return ok(copy(this.offered.get(actor.providerId) ?? EMPTY)); }
  async runs(actor: Actor) { return this.mine(actor, this.runRows); }
  async experiments(actor: Actor) { return this.mine(actor, this.experimentRows); } // runs are shared rows: always current
  async subscriptions(actor: Actor) { return this.mine(actor, this.subscriptionRows); }

  /** The same id with the same body is the record already made; with another body, a conflict. */
  private once<T>(rows: Owned<T>[], actor: Actor, id: (row: T) => string, key: string, body: string): Result<T> | null {
    const seen = rows.find((r) => r.providerId === actor.providerId && id(r.row) === key);
    if (seen === undefined) return null;
    return seen.body === body ? ok(copy(seen.row)) : no("conflict");
  }

  async launch(actor: Actor, launch: Launch): Promise<Result<Experiment>> {
    this.calls.push(["launch", actor, launch]);
    if (!holds(actor.role, "run_evaluation")) return no("denied");
    const body = JSON.stringify(launch);
    const seen = this.once(this.experimentRows, actor, (e) => e.experiment_id, launch.experiment_id, body);
    if (seen !== null) return seen;
    const c = this.offered.get(actor.providerId) ?? EMPTY;
    const has = (options: { ref: string }[], ref: string) => options.some((o) => o.ref === ref);
    if (!has(c.datasets, launch.dataset_ref) || !has(c.harnesses, launch.harness_ref) || !has(c.evaluators, launch.evaluator_ref)
      || !has(c.servings, launch.baseline_serving_ref) || !has(c.servings, launch.candidate_serving_ref)) return no("invalid");
    const queued = (): Run => {
      const run_id = randomUUID();
      const row: Run = { run_id, run_ref: `lab:run:${actor.providerId}:${run_id}@sha256:${"0".repeat(64)}`, dataset_ref: launch.dataset_ref,
        state: "queued", cases: { pending: launch.max_cases }, attempts: {}, costs: {} };
      this.runRows.push({ providerId: actor.providerId, row });
      return row;
    };
    const e: Experiment = { experiment_id: launch.experiment_id, created_at: new Date().toISOString(), protocol: launch.protocol, baseline: queued(), candidate: queued(), report: null };
    this.experimentRows.push({ providerId: actor.providerId, row: e, body });
    return ok(copy(e));
  }

  async cancel(actor: Actor, runId: string): Promise<Result<Run>> {
    this.calls.push(["cancel", actor, runId]);
    const r = this.runRows.find((x) => x.row.run_id === runId && x.providerId === actor.providerId);
    if (r === undefined) return no("not_found");
    if (!holds(actor.role, "run_evaluation")) return no("denied");
    if (r.row.state !== "queued" && r.row.state !== "running") return no("conflict");
    r.row.state = "cancelled";
    return ok(copy(r.row));
  }

  async subscribe(actor: Actor, request: SubscriptionRequest): Promise<Result<Subscription>> {
    this.calls.push(["subscribe", actor, request]);
    if (!holds(actor.role, "run_evaluation")) return no("denied");
    const body = JSON.stringify(request);
    const seen = this.once(this.subscriptionRows, actor, (s) => s.subscription_id, request.subscription_id, body);
    if (seen !== null) return seen;
    const mine = [request.external_run_ref, request.dataset_ref, request.harness_ref, request.evaluator_ref].every((ref) => ref.split(":")[2] === actor.providerId);
    const credit = request.run_limit.unit === "CREDIT" && request.limit.unit === "CREDIT" && exact(request.run_limit.value) <= exact(request.limit.value);
    if (!mine || !credit) return no("invalid");
    const s: Subscription = { ...request, decisions: [] };
    this.subscriptionRows.push({ providerId: actor.providerId, row: s, body });
    return ok(copy(s));
  }

  offer(providerId: string, catalog: Catalog): void {
    this.offered.set(providerId, catalog);
  }
  /** B1's workers moving a run on. */
  progress(runId: string, patch: Partial<Omit<Run, "run_id">>): void {
    Object.assign(this.runRows.find((r) => r.row.run_id === runId)!.row, patch);
  }
  /** B2's report once both runs ended. */
  settle(experimentId: string, report: Report): void {
    this.experimentRows.find((e) => e.row.experiment_id === experimentId)!.row.report = report;
  }
  /** B3's handler deciding a checkpoint for a subscription. */
  decide(subscriptionId: string, decision: Decision): void {
    this.subscriptionRows.find((s) => s.row.subscription_id === subscriptionId)!.row.decisions.push(decision);
  }
}
