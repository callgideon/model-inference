// R4: an in-memory stand-in for the releases surface (the WR-R4-1 shape), for tests/r and the labelled
// dev preview. It enforces what D9/R2 must: provider scoping (another provider's release is
// not_found), the role's capability (denied), the fence CAS (a stale page is conflict), one pending
// proposal per release, expansion only on an expand verdict. `step` is R2's controller pass and
// `decide` the operator's R2 approve/emergency_rollback: the Lab itself never calls either.
import { randomUUID } from "node:crypto";
import { holds, type Actor, type Decision, type Proposal, type ProposalKind, type Records, type Release, type ReleasesPort, type Result, type Variant, type Verdict } from "./port.ts";

type Owned<T> = T & { providerId: string };
const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const no = <T>(reason: "denied" | "not_found" | "conflict"): Result<T> => ({ ok: false, reason });
const strip = <T>({ providerId, ...rest }: Owned<T>): T => (void providerId, rest as T);

export class FakeReleases implements ReleasesPort {
  readonly calls: unknown[][] = [];
  private rows: Owned<Release>[] = [];
  private log: Owned<Decision>[] = [];
  private requests: Owned<Proposal>[] = [];
  private variantRows: Owned<Variant>[] = [];
  private readonly now = () => new Date().toISOString();

  seed(providerId: string, release: Release): void { this.rows.push({ providerId, ...structuredClone(release) }); }
  seedVariant(providerId: string, variant: Variant): void { this.variantRows.push({ providerId, ...structuredClone(variant) }); }

  private mine<T>(actor: Actor, rows: Owned<T>[]): T[] {
    return rows.filter((r) => r.providerId === actor.providerId).map((r) => structuredClone(strip(r)));
  }
  async releases(actor: Actor): Promise<Result<Records>> {
    return ok({ releases: this.mine(actor, this.rows), decisions: this.mine(actor, this.log), proposals: this.mine(actor, this.requests) });
  }
  async variants(actor: Actor): Promise<Result<Variant[]>> { return ok(this.mine(actor, this.variantRows)); }

  async propose(actor: Actor, kind: ProposalKind, policyRef: string, fence: number): Promise<Result<Proposal>> {
    this.calls.push(["propose", actor, kind, policyRef, fence]);
    const r = this.rows.find((x) => x.policyRef === policyRef && x.providerId === actor.providerId);
    if (r === undefined) return no("not_found");
    if (!holds(actor.role, "propose_publication")) return no("denied");
    const fits = r.fence === fence && (kind === "expand" ? r.state === "running" && r.verdict?.action === "expand" : r.state !== "rolled_back");
    if (!fits || this.requests.some((p) => p.policyRef === policyRef && p.state === "proposed")) return no("conflict");
    const p: Owned<Proposal> = { providerId: actor.providerId, proposalId: randomUUID(), kind, policyRef, fence, state: "proposed", proposedAt: this.now(), decidedAt: null };
    this.requests.push(p);
    return ok(strip(p));
  }

  /** D9's CAS: one transition bumps the fence and records the decision. */
  private transition(r: Owned<Release>, to: Release["state"], decision: Decision["decision"], by: string, reasons: string[], evidenceRefs: string[], at: string): void {
    Object.assign(r, { state: to, fence: r.fence + 1 });
    this.log.push({ providerId: r.providerId, policyRef: r.policyRef, decision, reasons, evidenceRefs, decidedBy: by, decidedAt: at });
  }

  /** R2's controller pass: a rollback verdict on a running release is one transition; nothing else moves. */
  step(policyRef: string, verdict: Verdict): void {
    const r = this.rows.find((x) => x.policyRef === policyRef)!;
    if (r.state !== "running") return;
    r.verdict = verdict;
    if (verdict.action === "rollback") this.transition(r, "rolled_back", "rollback", "controller", verdict.reasons, verdict.evidenceRefs, verdict.evaluatedAt);
  }

  /** The operator's decision (outside the Lab). A proposal whose release has moved on is rejected untouched. */
  decide(proposalId: string, approve: boolean, operatorId: string): "ok" | "conflict" {
    const p = this.requests.find((x) => x.proposalId === proposalId && x.state === "proposed");
    if (p === undefined) throw new Error("no such pending proposal");
    const r = this.rows.find((x) => x.policyRef === p.policyRef)!;
    const stale = approve && r.fence !== p.fence;
    Object.assign(p, { state: approve && !stale ? "approved" : "rejected", decidedAt: this.now() });
    if (stale) return "conflict";
    if (!approve) return "ok";
    if (p.kind === "expand") this.transition(r, "approved", "expand", operatorId, [], r.verdict!.evidenceRefs, p.decidedAt!);
    else this.transition(r, "rolled_back", "rollback", operatorId, ["operator:proposal"], [], p.decidedAt!);
    return "ok";
  }
}
