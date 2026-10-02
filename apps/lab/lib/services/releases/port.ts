// UX-10: the publication evidence the Releases page reads through the generated Lab client
// (lib/api, @infrx/api-client/lab), as the signed-in user and the session's workspace: AP-06's
// publication requests (`/lab/v1/control/proposals`), the deployment records they name, and AP-05's
// readiness of each requested revision. Reads only; an infrx operator decides every request
// (`/operator/v1/...` is not the Lab's). Rollout releases and variants stay on R4's port (rollouts/).
import type { components } from "@infrx/api-client/lab";
import type { LabApi, Result } from "../../api/index.ts";
import type { Actor } from "../../auth/access.ts";
import { sessionApi } from "../../auth/session.ts";

type S = components["schemas"];
export type Publication = {
  proposals: Result<S["Rows_Proposal_"]>;
  deployments: Result<S["Rows_Deployment_"]>;
  /** AP-05's readiness by requested deployment revision (none read when no request was listed). */
  readiness: Record<string, Result<S["ReadinessDoc"]>>;
};

const UNCONFIGURED = { ok: false, requestId: "", error: { kind: "unavailable", status: null, reason: "network" } } as const;

export async function readPublication(actor: Pick<Actor, "providerId">, api: LabApi | null = sessionApi()): Promise<Publication> {
  if (api === null) return { proposals: UNCONFIGURED, deployments: UNCONFIGURED, readiness: {} };
  const query = { provider_org_id: actor.providerId };
  const [proposals, deployments] = await Promise.all([
    api.call("get", "/lab/v1/control/proposals", { query }),
    api.call("get", "/lab/v1/control/deployments", { query }),
  ]);
  // ponytail: one readiness read per requested revision (a provider's few requests); a batch read when AP-05 has one.
  const ids = proposals.ok ? [...new Set(proposals.data.data.map((p) => p.deployment_revision_id))] : [];
  const reads = await Promise.all(ids.map((id) =>
    api.call("get", "/lab/v1/control/deployments/{deployment_id}/readiness", { params: { deployment_id: id }, query })));
  return { proposals, deployments, readiness: Object.fromEntries(ids.map((id, i) => [id, reads[i]])) };
}
