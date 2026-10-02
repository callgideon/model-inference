import { Fragment } from "react";
import Link from "next/link";
import { holds } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { proposeChange } from "@/lib/services/control/actions";
import { controlPort } from "@/lib/services/control/port";
import { DECISION, deploymentRows, operateActions, readiness, RECORD_STATE, recordedSmoke, refusalCopy, REFUSAL_COPY } from "@/lib/services/control/view";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClass } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import s from "../operate.module.css";

export const metadata = { title: "Deployments · infrx Lab" };

// UX-03 L-04: deployment records, their readiness only as far as evidence exists here, and publication
// requests. A record is "Registered", never healthy; a recorded smoke never verifies an engine; a
// request is a request (an infrx operator decides). The outcome of a request is whatever the records
// say after the redirect back here; a refusal is fixed copy (?refused=).
export default async function Deployments({ searchParams }: PageProps<"/deployments">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const port = controlPort();
  const [deployments, proposals] = await Promise.all([port.deployments(workspace), port.proposals(workspace)]);
  const known = proposals.ok ? proposals.value : null;
  const mayManage = holds(workspace.role, "manage_dev_deployment");
  const mayPropose = holds(workspace.role, "propose_publication");
  const retry = <Link href="/deployments" className={buttonClass()}>Try again</Link>;
  return (
    <>
      <PageHeader
        title="Deployments"
        purpose="Deployment records, the evidence behind them and publication requests. Connection details are provided after serving setup is verified."
      />
      {refused && <p role="alert">{refused}</p>}
      {!deployments.ok ? (
        <ServiceState state="unavailable" title="We couldn't load deployments" explanation={REFUSAL_COPY[deployments.reason]} action={retry} />
      ) : deployments.value.length === 0 ? (
        <ServiceState
          state="empty"
          title="No deployments yet"
          explanation="A deployment record appears when a model revision is registered. Registering does not start a serving engine."
          action={mayManage ? <Link href="/models/new" className={buttonClass("primary")}>Add model</Link> : undefined}
        />
      ) : (
        <ul className={s.records} aria-label="Deployment records">
          {deploymentRows(workspace.role, deployments.value, known ?? []).map((row, i) => {
            const d = deployments.value[i];
            const actions = known === null ? [] : operateActions(row);
            const openDev = d.state === "active" && d.environment === "dev";
            return (
              <li key={row.id} className={s.record}>
                <div className={s.recordHead}>
                  <h3>{d.modelId} {d.revisionLabel}</h3>
                  <Badge tone={RECORD_STATE[d.state].tone}>{RECORD_STATE[d.state].label}</Badge>
                  <Badge>{row.where}</Badge>
                </div>
                <p className={s.muted}>{recordedSmoke(d)}</p>
                {row.pending && <p>{row.pending}</p>}
                <details>
                  <summary>Readiness and checks</summary>
                  <dl className={s.facts}>
                    {readiness(d, known).map((c) => (
                      <Fragment key={c.stage}>
                        <dt>{c.stage}</dt>
                        <dd><Badge tone={c.tone}>{c.status}</Badge></dd>
                      </Fragment>
                    ))}
                  </dl>
                </details>
                <details>
                  <summary>Configuration</summary>
                  <dl className={s.facts}>
                    <dt>Deployment revision</dt>
                    <dd><code className="lab-id">{row.id}</code> <CopyButton value={row.id} label={`Copy deployment revision id of ${d.modelId} ${d.revisionLabel}`} /></dd>
                    <dt>Serving version</dt><dd className="lab-id">{row.serving}</dd>
                    <dt>Runtime</dt><dd className="lab-id">{row.runtime}</dd>
                    <dt>Schema</dt><dd className="lab-id">{row.schema}</dd>
                    <dt>Rate card</dt><dd className="lab-id">{row.rate}</dd>
                    <dt>Created</dt><dd>{d.createdAt}</dd>
                  </dl>
                </details>
                {openDev && mayManage && (
                  <p className={s.muted}>Dev smoke runs are unavailable here until serving readiness checks exist; the earlier check could leave a record validating.</p>
                )}
                {actions.includes("publish") && (
                  <details>
                    <summary className={buttonClass("primary")}>Request publication</summary>
                    <div className="lab-stack">
                      <dl className={s.facts}>
                        <dt>Model</dt><dd className="lab-id">{d.modelId} {d.revisionLabel}</dd>
                        <dt>Environment</dt><dd>{row.where}</dd>
                        <dt>Evidence</dt><dd>{recordedSmoke(d)} Serving readiness: not verified here.</dd>
                      </dl>
                      <p>An infrx operator approves or rejects it. Until then nothing becomes public.</p>
                      <form action={proposeChange}>
                        <input type="hidden" name="deploymentRevisionId" value={row.id} />
                        <input type="hidden" name="kind" value="publish" />
                        <Button type="submit" variant="primary">Confirm publication request</Button>
                      </form>
                    </div>
                  </details>
                )}
                {openDev && !mayPropose && <p className={s.muted}>Requesting publication needs an administrator.</p>}
              </li>
            );
          })}
        </ul>
      )}
      <section className={s.section} aria-labelledby="publication-heading" id="publication">
        <h2 id="publication-heading">Publication requests</h2>
        {!proposals.ok ? (
          <ServiceState state="unavailable" title="We couldn't load publication requests" explanation={REFUSAL_COPY[proposals.reason]} action={retry} />
        ) : proposals.value.length === 0 ? (
          <ServiceState state="empty" title="No publication requests yet" explanation="An administrator requests publication; an infrx operator approves or rejects it." />
        ) : (
          <ul className={s.records}>
            {proposals.value.map((p) => (
              <li key={p.proposalId} className={s.record}>
                <div className={s.recordHead}>
                  <h3>{p.kind === "publish" ? "Publication" : "Rollback"} request</h3>
                  <Badge tone={DECISION[p.state].tone}>{DECISION[p.state].status}</Badge>
                </div>
                <dl className={s.facts}>
                  <dt>Deployment revision</dt><dd className="lab-id">{p.deploymentRevisionId}</dd>
                  <dt>Requested</dt><dd>{p.proposedAt}</dd>
                  <dt>Decided</dt><dd>{p.decidedAt ?? "Not yet"}</dd>
                </dl>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
