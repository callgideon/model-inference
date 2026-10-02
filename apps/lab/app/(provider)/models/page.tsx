import Link from "next/link";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { controlPort, holds } from "@/lib/services/control/port";
import { importedNames, modelRows, refusalCopy, REFUSAL_COPY } from "@/lib/services/control/view";
import { Badge } from "@/components/ui/badge";
import { buttonClass } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import { RevisionForm } from "./revision-form";
import s from "../operate.module.css";

export const metadata = { title: "Models · infrx Lab" };

// UX-03 L-03: the workspace's registered model revisions. A first model comes through Add model
// (models/new: project, import or upload, verification, serving setup); the four-field form below only
// adds a revision to a model already imported here, and never uploads weights.
export default async function Models({ searchParams }: PageProps<"/models">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const models = await controlPort().models(workspace);
  const mayManage = holds(workspace.role, "manage_dev_deployment");
  return (
    <>
      <PageHeader
        title="Models"
        purpose={mayManage ? "Registered model revisions in this workspace." : "Registered model revisions in this workspace. Adding a model or a revision needs a developer or administrator."}
        actions={mayManage ? <Link href="/models/new" className={buttonClass("primary")}>Add model</Link> : undefined}
      />
      {refused && <p role="alert">{refused}</p>}
      {!models.ok ? (
        <ServiceState state="unavailable" title="We couldn't load models" explanation={REFUSAL_COPY[models.reason]} action={<Link href="/models" className={buttonClass()}>Try again</Link>} />
      ) : models.value.length === 0 ? (
        <ServiceState state="empty" title="No models are registered in this workspace yet" explanation="Add model creates its project, imports or uploads and verifies its files, then sets up a serving revision. Registering does not start a serving engine." />
      ) : (
        <ul className={s.records} aria-label="Model revisions">
          {modelRows(models.value).map((m) => (
            <li key={`${m.modelId}@${m.revision}`} className={s.record}>
              <div className={s.recordHead}>
                <h3>{m.name}</h3> <Badge>{m.revision}</Badge>
              </div>
              <dl className={s.facts}>
                <dt>Runtime</dt><dd className="lab-id">{m.runtime}</dd>
                <dt>Registered</dt><dd>{m.registered}</dd>
              </dl>
              <details>
                <summary>Details</summary>
                <dl className={s.facts}>
                  <dt>Model ID</dt><dd className="lab-id">{m.modelId}</dd>
                  <dt>Artifact</dt>
                  <dd><code className="lab-id">{m.artifactDigest}</code> <CopyButton value={m.artifactDigest} label={`Copy artifact digest of ${m.name} ${m.revision}`} /></dd>
                  <dt>Schema</dt><dd className="lab-id">{m.schemaVersion}</dd>
                </dl>
              </details>
            </li>
          ))}
        </ul>
      )}
      {mayManage && models.ok && models.value.length > 0 && (
        <section className={s.section} aria-labelledby="revision">
          <h2 id="revision">New revision of an imported model</h2>
          <p className={s.muted}>For a model already imported into this workspace. Registration creates a private dev deployment record; nothing is public until an operator approves a publication request.</p>
          <RevisionForm names={importedNames(models.value)} />
        </section>
      )}
    </>
  );
}
