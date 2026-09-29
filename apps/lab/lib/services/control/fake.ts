// L4: an in-memory stand-in for L3 (the WR-L4-1 shape), for tests/l/ui and the labelled dev preview.
// It enforces what L3 does (E3L-F3, measured on the real L3): the role's capability in the caller's own
// workspace first (denied), then provider scoping (another provider's record, or a non-dev one, is not_found), a registration only of the workspace's model with
// operator-imported weights (not_found) over one of those digests (invalid), smoke before publish
// (conflict), a rollback never a provider proposal (invalid) and a repeated proposal answering the open
// one (R214). `importModel`, `decide` and `rollback` are the operator's side and `discoverable` is App
// discovery: the Lab itself never calls them.
import { randomUUID } from "node:crypto";
import { holds, type Actor, type Aggregate, type Capability, type ControlPort, type Deployment, type Model, type Proposal, type ProposalKind, type Registration, type Result } from "./port.ts";

type Owned<T> = T & { providerId: string };
const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const no = <T>(reason: "denied" | "not_found" | "invalid" | "conflict"): Result<T> => ({ ok: false, reason });
const strip = <T>({ providerId, ...rest }: Owned<T>): T => (void providerId, rest as T);

export class FakeControl implements ControlPort {
  readonly calls: unknown[][] = [];
  private imported: { providerId: string; modelId: string; weights: string[] }[] = [];
  private registered: Owned<Model>[] = [];
  private revisions: Owned<Deployment>[] = [];
  private requests: Owned<Proposal>[] = [];
  private health: Owned<Aggregate>[] = [];
  private cards = 0;
  private readonly now = () => new Date().toISOString();

  private mine<T>(actor: Actor, rows: Owned<T>[]): Result<T[]> {
    return ok(rows.filter((r) => r.providerId === actor.providerId).map(strip));
  }
  async models(actor: Actor) { return this.mine(actor, this.registered); }
  async deployments(actor: Actor) { return this.mine(actor, this.revisions); }
  async proposals(actor: Actor) { return this.mine(actor, this.requests); }
  async aggregates(actor: Actor) { return this.mine(actor, this.health); }

  /** L3's `_dev`: the role in the caller's own workspace first (a refusal that confirms nothing about the
   *  id), then only its own dev revision; anything else, another provider's included, is not_found. */
  private own(actor: Actor, id: string, capability: Capability): Owned<Deployment> | "not_found" | "denied" {
    if (!holds(actor.role, capability)) return "denied";
    return this.revisions.find((d) => d.deploymentRevisionId === id && d.providerId === actor.providerId && d.environment === "dev") ?? "not_found";
  }

  async register(actor: Actor, r: Registration): Promise<Result<Deployment>> {
    this.calls.push(["register", actor, r]);
    if (!holds(actor.role, "manage_dev_deployment")) return no("denied");
    const model = this.imported.find((m) => m.providerId === actor.providerId && m.modelId.split("/").at(-1) === r.name);
    if (model === undefined) return no("not_found"); // only a model of the workspace with imported weights
    if (!model.weights.includes(r.artifactDigest)) return no("invalid"); // over one of those digests
    const { modelId } = model;
    const revisionLabel = `r${this.registered.filter((m) => m.providerId === actor.providerId && m.modelId === modelId).length + 1}`;
    const at = this.now();
    this.registered.push({ providerId: actor.providerId, modelId, revisionLabel, artifactDigest: r.artifactDigest, schemaVersion: r.schemaVersion, runtime: r.runtime, registeredAt: at });
    const dev: Owned<Deployment> = {
      providerId: actor.providerId, deploymentRevisionId: randomUUID(), modelId, servingVersionId: randomUUID(), revisionLabel,
      runtime: r.runtime, schemaVersion: r.schemaVersion, rateCardVersion: null, environment: "dev", visibility: "private",
      state: "active", smoke: "none", createdAt: at,
    };
    this.revisions.push(dev);
    return ok(strip(dev));
  }

  async smoke(actor: Actor, id: string): Promise<Result<Deployment>> {
    this.calls.push(["smoke", actor, id]);
    const d = this.own(actor, id, "manage_dev_deployment");
    if (typeof d === "string") return no(d);
    if (d.state !== "active") return no("conflict");
    d.smoke = "passed"; // the fake engine always answers
    return ok(strip(d));
  }

  async propose(actor: Actor, kind: ProposalKind, id: string): Promise<Result<Proposal>> {
    this.calls.push(["propose", actor, kind, id]);
    if (!holds(actor.role, "propose_publication")) return no("denied");
    if (kind !== "publish") return no("invalid"); // a rollback is the operator's listing decision
    const d = this.own(actor, id, "propose_publication");
    if (typeof d === "string") return no(d);
    const open = this.requests.find((p) => p.deploymentRevisionId === id && p.state === "proposed");
    if (open !== undefined) return ok(strip(open)); // R214: a repeat answers the open proposal
    if (d.state !== "active" || d.smoke !== "passed") return no("conflict");
    const p: Owned<Proposal> = { providerId: actor.providerId, proposalId: randomUUID(), kind, deploymentRevisionId: id, state: "proposed", proposedAt: this.now(), decidedAt: null };
    this.requests.push(p);
    // E3L-F5: L3's proposal IS a new prod revision (its id), unlisted until approved: private, unpriced.
    this.revisions.push({ ...d, deploymentRevisionId: p.proposalId, environment: "prod", visibility: "private", rateCardVersion: null, createdAt: p.proposedAt });
    return ok(strip(p));
  }

  /** The operator's decision (outside the Lab). Approving a publish makes a new prod revision public. */
  decide(proposalId: string, approve: boolean): void {
    const p = this.requests.find((x) => x.proposalId === proposalId && x.state === "proposed");
    if (p === undefined) throw new Error("no such pending proposal");
    Object.assign(p, { state: approve ? "approved" : "rejected", decidedAt: this.now() });
    const r = this.revisions.find((x) => x.deploymentRevisionId === p.proposalId)!;
    if (!approve) {
      r.state = "retired"; // E3L-F4: a rejected proposal is a terminal prod/private/retired row
      return;
    }
    const live = this.revisions.filter((x) => x.modelId === r.modelId && x.providerId === r.providerId && x.visibility === "public");
    for (const x of live) x.visibility = "private";
    this.cards += 1;
    Object.assign(r, { visibility: "public", rateCardVersion: `rc-${this.cards}` });
  }

  /** The operator's import (outside the Lab): a model's weights and its first public revision, priced. */
  importModel(providerId: string, modelId: string, weights: string[]): Deployment {
    const at = this.now();
    this.imported.push({ providerId, modelId, weights });
    this.registered.push({ providerId, modelId, revisionLabel: "r1", artifactDigest: weights[0], schemaVersion: "chat.v1", runtime: "vllm@sha256:" + "0".repeat(64), registeredAt: at });
    const live: Owned<Deployment> = {
      providerId, deploymentRevisionId: randomUUID(), modelId, servingVersionId: randomUUID(), revisionLabel: "r1", runtime: "vllm@sha256:" + "0".repeat(64),
      schemaVersion: "chat.v1", rateCardVersion: "rc-0", environment: "prod", visibility: "public", state: "active", smoke: "passed", createdAt: at,
    };
    this.revisions.push(live);
    return strip(live);
  }

  /** The operator's rollback (outside the Lab): the model's previous prod revision is public again. */
  rollback(providerId: string, modelId: string): void {
    const prod = this.revisions.filter((x) => x.providerId === providerId && x.modelId === modelId && x.environment === "prod" && x.state === "active" && x.rateCardVersion !== null);
    const live = prod.find((x) => x.visibility === "public");
    const back = prod.filter((x) => x !== live).at(-1);
    if (live === undefined || back === undefined) throw new Error("nothing to roll back to");
    live.visibility = "private";
    back.visibility = "public";
  }

  /** App discovery: the public prod revisions of the model (one, when listing is right). */
  discoverable(modelId: string): string[] {
    return this.revisions.filter((d) => d.modelId === modelId && d.environment === "prod" && d.visibility === "public" && d.state === "active").map((d) => d.deploymentRevisionId);
  }

  observe(providerId: string, aggregate: Aggregate): void {
    this.health.push({ providerId, ...aggregate });
  }
}
