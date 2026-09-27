// L4: an in-memory stand-in for L3 (the WR-L4-1 shape), for tests/l/ui and the labelled dev preview.
// It enforces what L3 must: provider scoping (another provider's record is not_found), the role's
// capability (denied), smoke before publish and one pending proposal per revision (conflict). `decide`
// is the operator's side and `discoverable` is App discovery: the Lab itself never calls either.
import { randomUUID } from "node:crypto";
import { holds, type Actor, type Aggregate, type Capability, type ControlPort, type Deployment, type Model, type Proposal, type ProposalKind, type Registration, type Result } from "./port.ts";

type Owned<T> = T & { providerId: string };
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const no = <T>(reason: "denied" | "not_found" | "invalid" | "conflict"): Result<T> => ({ ok: false, reason });
const strip = <T>({ providerId, ...rest }: Owned<T>): T => (void providerId, rest as T);

export class FakeControl implements ControlPort {
  readonly calls: unknown[][] = [];
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

  /** Own and permitted: another provider's id is not_found before the role is even looked at. */
  private own(actor: Actor, id: string, capability: Capability): Owned<Deployment> | "not_found" | "denied" {
    const row = this.revisions.find((d) => d.deploymentRevisionId === id && d.providerId === actor.providerId);
    if (row === undefined) return "not_found";
    return holds(actor.role, capability) ? row : "denied";
  }

  async register(actor: Actor, r: Registration): Promise<Result<Deployment>> {
    this.calls.push(["register", actor, r]);
    if (!holds(actor.role, "manage_dev_deployment")) return no("denied");
    if (!DIGEST.test(r.artifactDigest)) return no("invalid");
    const revisionLabel = `r${this.registered.filter((m) => m.providerId === actor.providerId && m.modelId === r.name).length + 1}`;
    const at = this.now();
    this.registered.push({ providerId: actor.providerId, modelId: r.name, revisionLabel, artifactDigest: r.artifactDigest, schemaVersion: r.schemaVersion, runtime: r.runtime, registeredAt: at });
    const dev: Owned<Deployment> = {
      providerId: actor.providerId, deploymentRevisionId: randomUUID(), modelId: r.name, servingVersionId: randomUUID(), revisionLabel,
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
    if (d.environment !== "dev" || d.state !== "active") return no("conflict");
    d.smoke = "passed"; // the fake engine always answers
    return ok(strip(d));
  }

  async propose(actor: Actor, kind: ProposalKind, id: string): Promise<Result<Proposal>> {
    this.calls.push(["propose", actor, kind, id]);
    const d = this.own(actor, id, "propose_publication");
    if (typeof d === "string") return no(d);
    const fits = kind === "publish"
      ? d.environment === "dev" && d.state === "active" && d.smoke === "passed"
      : d.environment === "prod" && d.visibility === "public" && d.state === "active" && this.previous(d) !== undefined;
    if (!fits || this.requests.some((p) => p.deploymentRevisionId === id && p.state === "proposed")) return no("conflict");
    const p: Owned<Proposal> = { providerId: actor.providerId, proposalId: randomUUID(), kind, deploymentRevisionId: id, state: "proposed", proposedAt: this.now(), decidedAt: null };
    this.requests.push(p);
    return ok(strip(p));
  }

  private previous(d: Deployment): Owned<Deployment> | undefined {
    return this.revisions.filter((x) => x.modelId === d.modelId && x.environment === "prod" && x.state === "active" && x !== d).at(-1);
  }

  /** The operator's decision (outside the Lab). Approving a publish makes a prod revision public. */
  decide(proposalId: string, approve: boolean): void {
    const p = this.requests.find((x) => x.proposalId === proposalId && x.state === "proposed");
    if (p === undefined) throw new Error("no such pending proposal");
    Object.assign(p, { state: approve ? "approved" : "rejected", decidedAt: this.now() });
    if (!approve) return;
    const d = this.revisions.find((x) => x.deploymentRevisionId === p.deploymentRevisionId)!;
    const live = this.revisions.filter((x) => x.modelId === d.modelId && x.providerId === d.providerId && x.visibility === "public");
    if (p.kind === "publish") {
      for (const x of live) x.visibility = "private";
      this.cards += 1;
      this.revisions.push({ ...d, deploymentRevisionId: randomUUID(), environment: "prod", visibility: "public", rateCardVersion: `rc-${this.cards}`, createdAt: this.now() });
    } else {
      const back = this.previous(d)!;
      Object.assign(d, { visibility: "private", state: "retired" });
      back.visibility = "public";
    }
  }

  /** App discovery: the public prod revisions, as `<model>@<label>`. */
  discoverable(): string[] {
    return this.revisions.filter((d) => d.environment === "prod" && d.visibility === "public" && d.state === "active").map((d) => `${d.modelId}@${d.revisionLabel}`);
  }

  observe(providerId: string, aggregate: Aggregate): void {
    this.health.push({ providerId, ...aggregate });
  }
}
