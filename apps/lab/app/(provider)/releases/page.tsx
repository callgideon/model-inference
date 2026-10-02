import Link from "next/link";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { readPublication } from "@/lib/services/releases/port";
import { failure, publicationRows, refusalCopy, releaseViews, roleNote } from "@/lib/services/releases/view";
import { proposeRelease } from "@/lib/services/rollouts/actions";
import { isPreview, releasesPort } from "@/lib/services/rollouts/port";
import { REFUSAL_COPY } from "@/lib/services/rollouts/view";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClass } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import { PreviewNote } from "@/components/preview-note";
import s from "../operate.module.css";
import { PURPOSE } from "./purpose";

export const metadata = { title: "Releases · infrx Lab" };

const DENIED = "Your role in this workspace does not allow reading these records.";

// UX-10 (L-11): controlled releases from R4's records and publication requests from AP-06's, each beside
// its own serving evidence (the latest observation; AP-05's readiness). Sections load and fail on their
// own. A proposal names the revision the server loaded; its outcome is whatever the records say after
// the redirect back here; a refusal is fixed copy (?refused=). No launch or traffic control.
export default async function Releases({ searchParams }: PageProps<"/releases">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const [records, publication] = await Promise.all([releasesPort().releases(workspace), readPublication(workspace)]);
  const note = roleNote(workspace.role);
  const retry = <Link href="/releases" className={buttonClass()}>Try again</Link>;
  const requests = publicationRows(publication);
  return (
    <>
      <PageHeader title="Releases" purpose={PURPOSE} actions={<Link href="/optimizations" className={buttonClass()}>Optimization comparisons</Link>} />
      {isPreview() && <PreviewNote records="release" service="rollout" />}
      {refused && <p role="alert">{refused}</p>}
      <section className={s.section} aria-labelledby="rollouts-heading">
        <h2 id="rollouts-heading">Controlled releases</h2>
        {!records.ok ? (
          failure(records) === "denied"
            ? <ServiceState state="denied" title="Releases are not available to your role" explanation={DENIED} />
            : <ServiceState state="unavailable" title="We couldn't load releases" explanation={REFUSAL_COPY.unavailable} action={retry} />
        ) : records.value.releases.length === 0 ? (
          <ServiceState state="empty" title="No releases yet" explanation="An infrx operator launches a shadow or canary release from its frozen plan; it appears here with its evidence." />
        ) : (
          <>
            {note && <p className={s.muted}>{note}</p>}
            <div className={s.records}>
              {releaseViews(workspace.role, records.value).map((r) => (
                <section key={r.id} aria-label={r.id}>
                  <div className={s.record}>
                    <div className={s.recordHead}>
                      <h3>{r.setup}</h3>
                      <Badge tone={r.proof.tone}>{r.proof.label}</Badge>
                    </div>
                    <dl className={s.facts}>
                      <dt>Serving proof</dt><dd>{r.proof.text}</dd>
                      <dt>Policy</dt><dd><code className="lab-id">{r.id}</code></dd>
                      <dt>Status</dt><dd>{r.status}</dd>
                      {r.blocked && (<><dt>Promotion</dt><dd>{r.blocked}</dd></>)}
                      <dt>Request</dt><dd>{r.pending ?? "—"}</dd>
                      <dt>Observed through</dt><dd>{r.observed}</dd>
                      <dt>Traffic</dt><dd>{r.traffic}</dd>
                    </dl>
                    <details>
                      <summary>Evidence</summary>
                      <dl className={s.facts}>
                        <dt>Baseline</dt><dd className="lab-id">{r.baseline}</dd>
                        <dt>Candidates</dt><dd className="lab-id">{r.candidates.join("; ")}</dd>
                        <dt>Configured limits</dt><dd>{r.plan}</dd>
                        <dt>Candidate errors (observed)</dt><dd>{r.errors}</dd>
                        <dt>Candidate p99 (observed)</dt><dd>{r.p99}</dd>
                        <dt>Quality coverage</dt><dd>{r.quality}</dd>
                        <dt>Spend</dt><dd>{r.spend}</dd>
                        <dt>Assignments</dt><dd className="lab-id">{r.assignments.length ? r.assignments.join("; ") : "—"}</dd>
                      </dl>
                      <h4>Decisions</h4>
                      {r.lineage.length === 0 ? <p>No decisions yet.</p> : <ol>{r.lineage.map((l) => <li key={l} className="lab-id">{l}</li>)}</ol>}
                    </details>
                    {r.proposals.map((p) => (
                      <div key={p.kind} className="lab-stack">
                        <p id={`${p.kind}-${r.fence}-${r.id}`} className="lab-id">{p.summary}</p>
                        <form action={proposeRelease}>
                          <input type="hidden" name="policyRef" value={r.id} />
                          <input type="hidden" name="fence" value={r.fence} />
                          <input type="hidden" name="kind" value={p.kind} />
                          <Button type="submit" variant={p.kind === "rollback" ? "danger" : "secondary"} aria-describedby={`${p.kind}-${r.fence}-${r.id}`}>{p.label}</Button>
                        </form>
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </>
        )}
      </section>
      <section className={s.section} aria-labelledby="publication-heading">
        <h2 id="publication-heading">Publication requests</h2>
        {!publication.proposals.ok ? (
          failure(publication.proposals) === "denied"
            ? <ServiceState state="denied" title="Publication requests are not available to your role" explanation={DENIED} />
            : <ServiceState state="unavailable" title="We couldn't load publication requests" explanation="Nothing is shown until they can be read; try again shortly." action={retry} />
        ) : requests.length === 0 ? (
          <ServiceState state="empty" title="No publication requests yet" explanation="An administrator requests publication from Deployments; an infrx operator approves or rejects it." />
        ) : (
          <ul className={s.records}>
            {requests.map((p) => (
              <li key={p.id} className={s.record}>
                <div className={s.recordHead}>
                  <h3>{p.title}</h3>
                  <Badge tone={p.request.tone}>{p.request.text}</Badge>
                  <Badge tone={p.proof.tone}>{p.proof.label}</Badge>
                  {p.listed && <Badge>{p.listed}</Badge>}
                </div>
                <dl className={s.facts}>
                  <dt>Serving proof</dt><dd>{p.proof.text}</dd>
                  <dt>Deployment revision</dt><dd><code className="lab-id">{p.revision}</code></dd>
                  <dt>Requested</dt><dd>{p.requested}</dd>
                  <dt>Decided</dt><dd>{p.decided}</dd>
                </dl>
                {p.caveat && <p className={s.muted}>{p.caveat}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
