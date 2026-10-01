// L4: rows and copy derived only from control records. Nothing here remembers what a button did.
import { fixedCopy } from "../common.ts";
import type { Role } from "../../auth/access.ts";
import { holds, REFUSALS, type Aggregate, type Deployment, type Proposal, type Refusal } from "./port.ts";

export type Action = "smoke" | "publish";
export type DeploymentRow = {
  id: string; model: string; serving: string; runtime: string; schema: string; rate: string; where: string;
  state: string; smoke: string; pending: string | null; actions: Action[];
};

export function deploymentRows(role: Role, deployments: Deployment[], proposals: Proposal[]): DeploymentRow[] {
  return deployments.map((d) => {
    const pending = proposals.find((p) => p.deploymentRevisionId === d.deploymentRevisionId && p.state === "proposed");
    const live = d.state === "active";
    const actions: Action[] = [];
    if (live && d.environment === "dev" && holds(role, "manage_dev_deployment")) actions.push("smoke");
    if (live && !pending && holds(role, "propose_publication") && d.environment === "dev" && d.smoke === "passed") actions.push("publish"); // a rollback is the operator's (E3L-F3)
    return {
      id: d.deploymentRevisionId, model: d.modelId, serving: `${d.servingVersionId}@${d.revisionLabel}`, runtime: d.runtime,
      schema: d.schemaVersion, rate: d.rateCardVersion ?? "unpriced", where: `${d.environment} · ${d.visibility}`,
      state: d.state, smoke: d.smoke, pending: pending ? `${pending.kind} proposed · awaiting operator approval` : null, actions,
    };
  });
}

export type HealthRow = { deployment: string; window: string; requests: string; errorRate: string; p95: string };

/** Named fields only: anything else a record carries (an identity, content) is never rendered. */
export function healthRows(aggregates: Aggregate[]): HealthRow[] {
  return aggregates.map((a) => ({
    deployment: a.deploymentRevisionId,
    window: `${a.windowStart} – ${a.windowEnd}`,
    requests: String(a.requests),
    errorRate: a.requests > 0 ? `${Math.round((a.errors / a.requests) * 1000) / 10}%` : "—",
    p95: a.p95LatencyMs === null ? "—" : `${a.p95LatencyMs} ms`,
  }));
}

export const REFUSAL_COPY: Record<Refusal, string> = {
  denied: "Your role in this workspace does not allow that.",
  not_found: "That record is not in this workspace.",
  invalid: "Those values were not accepted. Check the digest, name, schema and runtime.",
  conflict: "That record has moved on or already has a pending request; this page shows its current state.",
  unavailable: "Control records could not be read. Nothing is shown until they can be; try again shortly.",
};

/** `?refused=` is anyone's to write: only a known reason's fixed copy is ever shown (common.ts). */
export const refusalCopy = fixedCopy(REFUSALS, REFUSAL_COPY);
