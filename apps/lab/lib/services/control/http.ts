// WR-E3L-J: the control port over the Lab control service's `/lab/v1/control` (R186's factory,
// routes/lab_control.py), on the shared transport (../http.ts): the credential is the session's own access token
// (none: nothing is sent), the provider is the actor's (`provider_org_id`; the route re-derives both from
// the session and L2), snake_case keys become the port's camelCase and no value is touched. Lists come as
// `{data}`; a refusal is the route's status mapped to the port's reason; an answer holding one record the
// pages cannot read is unavailable (fails closed).
import { camel, labClient, list, nul, num, obj, oneOf, str, type HttpOptions } from "../http.ts";
import type { ControlPort } from "./port.ts";

export type { HttpOptions };

const MODEL = obj({ modelId: str, revisionLabel: str, artifactDigest: str, schemaVersion: str, runtime: str, registeredAt: str });
const DEPLOYMENT = obj({
  deploymentRevisionId: str, modelId: str, servingVersionId: str, revisionLabel: str, runtime: str, schemaVersion: str,
  rateCardVersion: nul(str), environment: oneOf("dev", "prod"), visibility: oneOf("private", "public"), state: oneOf("active", "retired"),
  smoke: oneOf("none", "passed", "failed"), createdAt: str,
});
const PROPOSAL = obj({
  proposalId: str, kind: oneOf("publish", "rollback"), deploymentRevisionId: str, state: oneOf("proposed", "approved", "rejected"), proposedAt: str, decidedAt: nul(str),
});
const AGGREGATE = obj({ deploymentRevisionId: str, windowStart: str, windowEnd: str, requests: num, errors: num, p95LatencyMs: nul(num) });

export function httpControl(options: HttpOptions): ControlPort {
  const { get, post } = labClient(options, "/lab/v1/control", { rename: camel });
  return {
    models: (actor) => get(actor, "models", list(MODEL)),
    deployments: (actor) => get(actor, "deployments", list(DEPLOYMENT)),
    proposals: (actor) => get(actor, "proposals", list(PROPOSAL)),
    aggregates: (actor) => get(actor, "aggregates", list(AGGREGATE)),
    register: (actor, r) => post(actor, "register", DEPLOYMENT,
      { name: r.name, artifact_digest: r.artifactDigest, schema_version: r.schemaVersion, runtime: r.runtime }),
    smoke: (actor, id) => post(actor, `deployments/${encodeURIComponent(id)}/smoke`, DEPLOYMENT),
    propose: (actor, kind, id) => post(actor, "proposals", PROPOSAL, { kind, deployment_revision_id: id }),
  };
}
