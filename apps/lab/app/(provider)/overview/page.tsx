import Link from "next/link";
import { holds } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { controlPort } from "@/lib/services/control/port";
import { observedThrough, recordCounts, setupStages, STAGE_BADGE, trafficRows } from "@/lib/services/control/view";
import { Badge } from "@/components/ui/badge";
import { buttonClass } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import s from "../operate.module.css";

export const metadata = { title: "Overview · infrx Lab" };

const retry = <Link href="/overview" className={buttonClass()}>Try again</Link>;

// UX-03 L-02: the setup stages and counts from the control records, measured traffic from the
// aggregates; each section loads and fails on its own, and a failed read is never shown as a zero.
export default async function Overview() {
  const workspace = await requireProviderWorkspace();
  const port = controlPort();
  const [models, deployments, proposals, aggregates] = await Promise.all([
    port.models(workspace), port.deployments(workspace), port.proposals(workspace), port.aggregates(workspace),
  ]);
  const loadedAt = new Date().toISOString();
  const mayAdd = holds(workspace.role, "manage_dev_deployment");
  return (
    <>
      <PageHeader
        title="Overview"
        purpose={
          <>
            {workspace.providerName}: your models, deployments and next steps.
            {!mayAdd && " Your role can read this workspace; adding a model needs a developer or administrator."}
          </>
        }
        actions={mayAdd ? <Link href="/models/new" className={buttonClass("primary")}>Add model</Link> : undefined}
      />
      <section className={s.section} aria-labelledby="setup">
        <h2 id="setup">Set up</h2>
        <ol className={s.steps}>
          {setupStages(models, proposals).map((stage) => (
            <li key={stage.title}>
              <strong>{stage.title}</strong> <Badge tone={STAGE_BADGE[stage.state].tone}>{STAGE_BADGE[stage.state].label}</Badge>
              <p>{stage.detail}</p>
            </li>
          ))}
        </ol>
      </section>
      <section className={s.section} aria-labelledby="records">
        <h2 id="records">Records</h2>
        {(!models.ok || !deployments.ok || !proposals.ok) && (
          <ServiceState state="unavailable" title="We couldn't load control records" explanation="Counts that could not be read say so; nothing is shown as zero." action={retry} />
        )}
        <div className={s.counts}>
          {recordCounts(deployments, proposals).map((c) => (
            <Link key={c.label} href={c.href} className={s.count}>
              <span>{c.label}</span>
              <strong>{c.value ?? "Not available"}</strong>
            </Link>
          ))}
        </div>
      </section>
      <section className={s.section} aria-labelledby="traffic">
        <h2 id="traffic">Recent measured traffic</h2>
        {!aggregates.ok ? (
          <ServiceState state="unavailable" title="We couldn't load measured traffic" explanation="The other sections are unaffected." action={retry} />
        ) : aggregates.value.length === 0 ? (
          <ServiceState state="empty" title="No measured requests yet" explanation="Measured windows appear here once a deployment serves requests." />
        ) : (
          <>
            <div className={s.tableWrap} role="region" aria-label="Measured traffic" tabIndex={0}>
              <table className={s.table}>
                <thead>
                  <tr><th>Deployment</th><th>Window</th><th>Requests</th><th>Error rate</th><th>p95 latency</th></tr>
                </thead>
                <tbody>
                  {trafficRows(aggregates.value).map((r) => (
                    <tr key={`${r.deployment} ${r.window}`}>
                      <td className="lab-id">{r.deployment}</td><td>{r.window}</td>
                      {r.measured ? <><td>{r.requests}</td><td>{r.errorRate}</td><td>{r.p95}</td></> : <td colSpan={3}>No measured requests</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className={s.muted}>Observed through {observedThrough(aggregates.value)}</p>
          </>
        )}
      </section>
      <p className={s.muted}>Last loaded {loadedAt}</p>
    </>
  );
}
