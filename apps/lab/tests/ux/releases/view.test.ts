// UX-10 (L-11, UX-T12's stale release fence): the release and optimization evidence view and the
// publication reads. Each case names the broken behaviour it catches.
import assert from "node:assert/strict";
import test from "node:test";
import { labApi } from "../../../lib/api/index.ts";
import { readPublication } from "../../../lib/services/releases/port.ts";
import {
  failure, NO_MEASUREMENT, publicationRows, readinessStatus, refusalCopy, releaseViews, roleNote, STALE_FENCE, variantViews,
} from "../../../lib/services/releases/view.ts";
import { REFUSAL_COPY } from "../../../lib/services/rollouts/view.ts";
import {
  A, AT, CAND, DENIED, DEP, DEP2, dep, DOWN, EXPAND, ident, NOT_FOUND, ok, POLICY, pub, ready, records, release, T, UNMOUNTED, variant,
} from "./fixtures.ts";

const ADMIN = "administrator" as const;

test("UX10-V01 no progress is No measurement yet, never zero traffic; a unit-refused release keeps its refusal", () => {
  const [none] = releaseViews(ADMIN, records([release({ progress: null, verdict: null })]));
  assert.equal(none.traffic, NO_MEASUREMENT);
  assert.equal(none.observed, NO_MEASUREMENT);
  assert.doesNotMatch(JSON.stringify(none), /\b0 requests|candidate 0/);
  const [refused] = releaseViews(ADMIN, records([release({ progress: null, verdict: null, refused: "unit_refused" })]));
  assert.equal(refused.traffic, "progress unavailable: settled in another unit");
  assert.match(refused.proof.text, /settled in another unit/);
  const [live] = releaseViews(ADMIN, records([release()]));
  assert.equal(live.observed, `through ${T}`);
  assert.equal(live.traffic, "candidate 120 · baseline 2280 requests");
});

test("UX10-V02 the current serving proof is the latest observation only: an approved expansion is never proof", () => {
  const approved = releaseViews(ADMIN, records([release({ state: "approved", progress: null, verdict: EXPAND })]))[0];
  assert.equal(approved.status, "expansion approved by an operator");
  assert.notEqual(approved.proof.tone, "success");
  assert.match(approved.proof.text, /^No measurement yet/);
  const healthy = releaseViews(ADMIN, records([release({ state: "approved", verdict: EXPAND })]))[0];
  assert.deepEqual(healthy.proof, { tone: "success", label: "Candidate healthy", text: `Candidate healthy in the observation through ${T}` });
  const sick = release();
  sick.progress!.candidateHealthy = false;
  assert.deepEqual(releaseViews(ADMIN, records([sick]))[0].proof, { tone: "danger", label: "Candidate unhealthy", text: `Candidate unhealthy in the observation through ${T}` });
});

test("UX10-V03 a proposal summary names the policy and the revision the server loaded, and says an operator decides", () => {
  const [row] = releaseViews(ADMIN, records([release({ verdict: EXPAND })]));
  assert.deepEqual(row.proposals.map((p) => p.kind), ["expand", "rollback"]);
  for (const p of row.proposals) {
    assert.ok(p.summary.includes(POLICY), "the policy");
    assert.ok(p.summary.includes("revision 3,"), "the fence the records carry, not the version");
    assert.match(p.summary, /An infrx operator decides; nothing changes until then\./);
  }
  assert.deepEqual(row.proposals.map((p) => p.label), ["Propose expansion", "Propose rollback"]);
});

test("UX10-V04 viewer and developer get no proposal and are told an administrator proposes; an administrator gets no note", () => {
  for (const role of ["viewer", "developer"] as const) {
    assert.deepEqual(releaseViews(role, records([release({ verdict: EXPAND })]))[0].proposals, [], role);
    assert.equal(roleNote(role), "Proposing an expansion or a rollback needs an administrator.", role);
  }
  assert.equal(roleNote(ADMIN), null);
});

test("UX10-V05 a stale fence asks for review again; any other ?refused= is the fixed copy, an unknown one nothing", () => {
  assert.equal(refusalCopy("conflict"), `${REFUSAL_COPY.conflict} ${STALE_FENCE}`);
  assert.match(STALE_FENCE, /newer policy revision/);
  assert.match(STALE_FENCE, /[Rr]eview .* again/);
  assert.equal(refusalCopy("denied"), REFUSAL_COPY.denied);
  assert.equal(refusalCopy("<script>"), null);
  assert.equal(refusalCopy(undefined), null);
});

test("UX10-S01 a refused read is denied only for 403/denied; anything else is unavailable, never empty", () => {
  assert.equal(failure({ ok: false, reason: "denied" }), "denied");
  assert.equal(failure({ ok: false, reason: "unavailable" }), "unavailable");
  assert.equal(failure({ ok: false, reason: "not_found" }), "unavailable");
  assert.equal(failure(DENIED), "denied");
  assert.equal(failure(NOT_FOUND), "unavailable");
  assert.equal(failure(UNMOUNTED), "unavailable");
  assert.equal(failure(DOWN), "unavailable");
});

test("UX10-U01 readiness: ready is proof with its time; not ready names the reasons; none recorded and unreachable differ", () => {
  assert.deepEqual(readinessStatus(ok(ready())), { tone: "success", label: "Serving proof current", text: `Serving proof current: identity, smoke and health checks passed (checked ${AT})` });
  assert.deepEqual(readinessStatus(ok(ready({ ready: false, reasons: ["health_expired", "expired"] }))), { tone: "warning", label: "No current serving proof", text: "No current serving proof: health_expired, expired" });
  assert.deepEqual(readinessStatus(NOT_FOUND), { tone: "neutral", label: "No serving proof recorded", text: "No serving proof recorded for this revision" });
  for (const r of [UNMOUNTED, DOWN, DENIED, undefined]) {
    assert.deepEqual(readinessStatus(r), { tone: "neutral", label: "Serving proof unknown", text: "Serving proof can't be checked right now" }, JSON.stringify(r));
  }
});

test("UX10-U02 a publication request shows its decision beside its revision's proof; an approval without proof says so", () => {
  const rows = publicationRows({
    proposals: ok({ data: [pub({ state: "approved", decided_at: AT }), pub({ proposal_id: "pp2", kind: "rollback", deployment_revision_id: DEP2, state: "rejected" })] }),
    deployments: ok({ data: [dep({ visibility: "public", environment: "prod" })] }),
    readiness: { [DEP]: UNMOUNTED, [DEP2]: ok(ready({ deployment_revision_id: DEP2 })) },
  });
  assert.equal(rows[0].title, "Publish acme/marlin-sop r2");
  assert.deepEqual(rows[0].request, { tone: "info", text: "Approved by an operator" });
  assert.equal(rows[0].proof.text, "Serving proof can't be checked right now");
  assert.equal(rows[0].caveat, "An approval is not proof that this revision is serving now.");
  assert.equal(rows[0].listed, "Listed publicly");
  assert.equal(rows[1].title, `Roll back deployment revision ${DEP2}`, "a revision the listing does not hold is named by its id");
  assert.deepEqual(rows[1].request, { tone: "warning", text: "Rejected by an operator" });
  assert.equal(rows[1].proof.tone, "success");
  assert.equal(rows[1].caveat, null);
  assert.equal(rows[1].listed, null);
  const proven = publicationRows({ proposals: ok({ data: [pub({ state: "approved" })] }), deployments: DOWN, readiness: { [DEP]: ok(ready()) } });
  assert.equal(proven[0].caveat, null, "an approval with current proof needs no caveat");
  assert.equal(proven[0].title, `Publish deployment revision ${DEP}`, "an unreadable listing still shows the request");
  const pending = publicationRows({ proposals: ok({ data: [pub()] }), deployments: DOWN, readiness: {} });
  assert.deepEqual(pending[0].request, { tone: "neutral", text: "Awaiting an operator's decision" });
  assert.equal(pending[0].caveat, null);
  assert.deepEqual(publicationRows({ proposals: DOWN, deployments: DOWN, readiness: {} }), []);
});

test("UX10-U03 the reads go through the generated client as the workspace: one readiness read per distinct revision", async () => {
  const seen: string[] = [];
  const answer = (url: string): unknown =>
    url.includes("/readiness") ? ready() : url.includes("/proposals") ? { data: [pub(), pub({ proposal_id: "pp2", kind: "rollback" }), pub({ proposal_id: "pp3", deployment_revision_id: DEP2 })] } : { data: [dep()] };
  const fetch = (async (url: string) => { seen.push(url); return new Response(JSON.stringify(answer(url)), { status: 200 }); }) as unknown as typeof globalThis.fetch;
  const got = await readPublication({ providerId: A }, labApi({ baseUrl: "http://api.test", fetch }));
  assert.ok(got.proposals.ok && got.deployments.ok);
  assert.deepEqual(Object.keys(got.readiness).sort(), [DEP, DEP2].sort());
  const q = `provider_org_id=${A}`;
  assert.deepEqual(seen.sort(), [
    `http://api.test/lab/v1/control/deployments/${DEP}/readiness?${q}`,
    `http://api.test/lab/v1/control/deployments/${DEP2}/readiness?${q}`,
    `http://api.test/lab/v1/control/deployments?${q}`,
    `http://api.test/lab/v1/control/proposals?${q}`,
  ].sort());
  const none = await readPublication({ providerId: A }, null);
  assert.deepEqual(none, { proposals: DOWN_UNCONFIGURED, deployments: DOWN_UNCONFIGURED, readiness: {} }, "no API configured: unavailable, nothing sent");
  const refused = await readPublication({ providerId: A }, labApi({ baseUrl: "http://api.test", fetch: (async () => new Response('{"refusal":"denied"}', { status: 403 })) as unknown as typeof globalThis.fetch }));
  assert.deepEqual(refused.readiness, {}, "no request listed: no readiness read");
});
const DOWN_UNCONFIGURED = { ok: false, requestId: "", error: { kind: "unavailable", status: null, reason: "network" } };

test("UX10-O01 an identity stored before it was recorded reads Not recorded, and its figures are not a comparison", () => {
  const [row] = variantViews([variant({ base: null })]);
  assert.equal(row.base, "Not recorded");
  assert.match(row.variant, /^vllm 0\.11\.2 on B300/);
  assert.equal(row.comparable, "Not comparable: an identity is not recorded");
  assert.equal(row.performance, "Not comparable: an identity is not recorded");
  assert.equal(row.claim, "no optimization claimed");
  assert.equal(variantViews([variant({ variant: null })])[0].variant, "Not recorded");
});

test("UX10-O02 a variant measured on other hardware is not comparable: no ratio, no claim", () => {
  const [row] = variantViews([variant({ variant: ident({ hardware: "L40S", quantization: "nvfp4" }) })]);
  assert.equal(row.comparable, "Not comparable: measured on different hardware (B300, L40S)");
  assert.doesNotMatch(row.performance, /×|throughput/);
  assert.equal(row.claim, "no optimization claimed");
  const [same] = variantViews([variant()]);
  assert.equal(same.comparable, null);
  assert.match(same.performance, /^measured: throughput ×1\.42/);
  assert.equal(same.claim, "optimization claimed for this scope only");
  const [unmeasured] = variantViews([variant({ base: null }, { performance: null })]);
  assert.equal(unmeasured.performance, "not measured", "nothing measured stays not measured");
});

test("UX10-O03 an optimization is its own serving version: the base is unchanged, and the report digest is shown", () => {
  const [row] = variantViews([variant()]);
  assert.ok(row.serving.startsWith(`${CAND} is a separate serving version`), row.serving);
  assert.match(row.serving, /is unchanged; it is replaced only through a release\.$/);
  assert.equal(row.digest, `sha256:${"e".repeat(64)}`);
  assert.equal(variantViews([variant({ comparison: null })])[0].digest, "No comparison report");
});
