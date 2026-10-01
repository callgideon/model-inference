// L4: the Lab's view of L3's control service (`/lab/v1/control`, WR-L4-1). With LAB_API_URL (else LAB_CONTROL_URL) set the
// port is the HTTP adapter (server.ts, WR-E3L-J); unset it is "unavailable" (fails closed), or, only
// outside production and only when asked for, the labelled preview fake.
import type { Actor } from "../../auth/access.ts";
import { down, previewPort } from "../common.ts";
import { FakeControl } from "./fake.ts";
import { labControl } from "./server.ts";

/** The one role table and actor (lib/auth/access.ts, contracts/v2 ROLE_CAPABILITIES). L3 re-checks every call. */
export { holds, type Actor, type Capability } from "../../auth/access.ts";
export type Registration = { name: string; artifactDigest: string; schemaVersion: string; runtime: string };
export type Model = { modelId: string; revisionLabel: string; artifactDigest: string; schemaVersion: string; runtime: string; registeredAt: string };
export type Deployment = {
  deploymentRevisionId: string;
  modelId: string;
  servingVersionId: string;
  revisionLabel: string;
  runtime: string;
  schemaVersion: string;
  rateCardVersion: string | null;
  environment: "dev" | "prod";
  visibility: "private" | "public";
  state: "active" | "retired";
  smoke: "none" | "passed" | "failed";
  createdAt: string;
};
export type ProposalKind = "publish" | "rollback";
export type Proposal = {
  proposalId: string;
  kind: ProposalKind;
  deploymentRevisionId: string;
  state: "proposed" | "approved" | "rejected";
  proposedAt: string;
  decidedAt: string | null;
};
/** L2's DeploymentAggregate: counts per own deployment and window, no identity of anyone. */
export type Aggregate = {
  deploymentRevisionId: string;
  windowStart: string;
  windowEnd: string;
  requests: number;
  errors: number;
  p95LatencyMs: number | null;
};
export const REFUSALS = ["denied", "not_found", "invalid", "conflict", "unavailable"] as const;
export type Refusal = (typeof REFUSALS)[number];
export type Result<T> = { ok: true; value: T } | { ok: false; reason: Refusal };

export interface ControlPort {
  models(actor: Actor): Promise<Result<Model[]>>;
  deployments(actor: Actor): Promise<Result<Deployment[]>>;
  proposals(actor: Actor): Promise<Result<Proposal[]>>;
  aggregates(actor: Actor): Promise<Result<Aggregate[]>>;
  /** Registers a model revision and its private dev deployment revision. */
  register(actor: Actor, registration: Registration): Promise<Result<Deployment>>;
  smoke(actor: Actor, deploymentRevisionId: string): Promise<Result<Deployment>>;
  /** A proposal only; an operator decides it outside the Lab. */
  propose(actor: Actor, kind: ProposalKind, deploymentRevisionId: string): Promise<Result<Proposal>>;
}

const UNAVAILABLE: ControlPort = { models: down, deployments: down, proposals: down, aggregates: down, register: down, smoke: down, propose: down };

/** The HTTP adapter when configured; otherwise unavailable, or outside production the asked-for preview (common.ts). */
export const { isPreview, port: controlPort } = previewPort("LAB_CONTROL_PREVIEW", () => new FakeControl(), labControl, UNAVAILABLE);
