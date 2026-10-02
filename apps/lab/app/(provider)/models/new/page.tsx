import { randomUUID } from "node:crypto";
import Link from "next/link";
import { holds } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { Badge } from "@/components/ui/badge";
import { buttonClass } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import { artifactPort } from "./api";
import { AutoRefresh, ImportForm, ProjectForm, SetupForm, UploadForm } from "./forms";
import { operationView, problem, step, STEPS, wizardQuery, type Step } from "./wizard";
import s from "../../operate.module.css";

export const metadata = { title: "Add model · infrx Lab" };

const back = (href: string, label: string) => <Link href={href} className={buttonClass()}>{label}</Link>;

function Steps({ current }: { current: Step }) {
  const at = STEPS.findIndex((x) => x.key === current);
  return (
    <ol className={s.steps} aria-label="Add model steps">
      {STEPS.map((x, i) => (
        <li key={x.key} aria-current={i === at ? "step" : undefined}>
          {x.label} {i < at || current === "done" ? <Badge tone="info">Done</Badge> : null}
        </li>
      ))}
    </ol>
  );
}

// AP-09 09d (03-lab L-03, contracts §4): project -> pinned repository import or browser upload ->
// server verification (the operation's own state) -> serving revision. Every step is read back from
// AP-04 by the ids in the URL; nothing is remembered in the browser. A serving revision is not a
// deployment: private deployment and readiness need the hosting API (AP-05) and say so.
export default async function AddModel({ searchParams }: PageProps<"/models/new">) {
  const workspace = await requireProviderWorkspace();
  const q = wizardQuery(await searchParams);
  const current = step(q);
  const api = artifactPort();
  const header = (
    <PageHeader
      title="Add model"
      breadcrumb={[{ href: "/models", label: "Models" }]}
      purpose="Create its project, bring in its files from a pinned repository or this browser, let the server verify them, then set up a serving revision."
    />
  );
  if (!holds(workspace.role, "manage_dev_deployment"))
    return <>{header}<ServiceState state="denied" title="Adding a model needs a developer or administrator" explanation="Your role can read this workspace's models; ask your provider administrator to add one." action={back("/models", "Back to models")} /></>;
  if (api === null)
    return <>{header}<ServiceState state="unavailable" title="Model import isn't available here yet" explanation="This Lab deployment does not offer the model import service yet, so a first model cannot be added from the Lab. An imported model's new revisions are on Models." action={back("/models", "Back to models")} /></>;

  // ponytail: the first 100 projects (and below, revisions); follow next_cursor when a workspace holds more.
  const projects = await api.call(workspace, "get", "/lab/v1/control/model-projects", { query: { limit: 100 } });
  const unread = (r: { ok: false; error: Parameters<typeof problem>[0] }, what: string) => (
    <ServiceState state={r.error.status === 403 || r.error.status === 401 ? "denied" : "unavailable"} title={`We couldn't read ${what}`} explanation={problem(r.error, false).message} action={back(`/models/new?${new URLSearchParams(Object.entries(q).filter((e): e is [string, string] => e[1] !== undefined))}`, "Try again")} />
  );
  let body: React.ReactNode;
  if (!projects.ok) body = unread(projects, "model projects");
  else if (current === "project") {
    body = (
      <>
        {projects.data.data.length > 0 && (
          <section className={s.section} aria-labelledby="existing">
            <h2 id="existing">Continue a project</h2>
            <ul className={s.rows}>
              {projects.data.data.map((p) => (
                <li key={p.project_id} className={s.row}>
                  <Link href={`/models/new?project=${encodeURIComponent(p.project_id)}`}>{p.name}</Link> <code className="lab-id">{p.slug}</code>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section className={s.section} aria-labelledby="new-project">
          <h2 id="new-project">New model project</h2>
          <p className={s.muted}>A project holds the verified files and serving revisions of one model. Creating it serves nothing.</p>
          <ProjectForm idempotencyKey={randomUUID()} />
        </section>
      </>
    );
  } else {
    const project = projects.data.data.find((p) => p.project_id === q.project);
    if (project === undefined) {
      body = <ServiceState state="not_found" title="That project is not in this workspace" explanation="Start from the list of this workspace's projects." action={back("/models/new", "Model projects")} />;
    } else if (current === "source") {
      body = (
        <>
          <p>Project <strong>{project.name}</strong> <code className="lab-id">{project.slug}</code></p>
          <div className={s.split}>
            <section className={s.section} aria-labelledby="import">
              <h2 id="import">Import from a pinned repository</h2>
              <ImportForm projectId={project.project_id} idempotencyKey={randomUUID()} />
            </section>
            <section className={s.section} aria-labelledby="upload">
              <h2 id="upload">Upload from this browser</h2>
              <UploadForm projectId={project.project_id} idempotencyKey={randomUUID()} />
            </section>
          </div>
        </>
      );
    } else if (current === "validation") {
      const op = await api.call(workspace, "get", "/lab/v1/operations/{operation_id}", { params: { operation_id: q.operation! } });
      if (!op.ok) body = unread(op, "this verification");
      else {
        const view = operationView(op.data);
        body = (
          <section className={s.section} aria-labelledby="verify">
            <h2 id="verify">Verification</h2>
            <p role="status"><Badge tone={view.tone}>{view.label}</Badge> {view.detail}</p>
            <p className={s.muted}>Operation <code className="lab-id">{op.data.operation_id}</code>, updated {op.data.updated_at}</p>
            <div className={s.actions}>
              {view.artifactId !== null && (
                <Link href={`/models/new?${new URLSearchParams({ project: project.project_id, artifact: view.artifactId })}`} className={buttonClass("primary")}>Continue to serving setup</Link>
              )}
              {!view.terminal && back(`/models/new?${new URLSearchParams({ project: project.project_id, operation: op.data.operation_id })}`, "Check again")}
              {view.terminal && view.artifactId === null && back(`/models/new?project=${encodeURIComponent(project.project_id)}`, "Back to the source step")}
            </div>
            {!view.terminal && <AutoRefresh seconds={op.data.retry_after_s ?? 2} />}
          </section>
        );
      }
    } else if (current === "setup") {
      const art = await api.call(workspace, "get", "/lab/v1/artifacts/{artifact_id}", { params: { artifact_id: q.artifact! } });
      if (!art.ok) body = unread(art, "this artifact");
      else {
        const a = art.data;
        const reasons = a.compatibility.reasons ?? [];
        body = (
          <section className={s.section} aria-labelledby="setup">
            <h2 id="setup">Serving setup</h2>
            <dl className={s.facts}>
              <dt>Source</dt><dd className="lab-id">{a.source === "import" ? `${a.source_repo} @ ${a.source_commit}` : a.source === "upload" ? "Uploaded files" : "Adopted by an operator"}</dd>
              <dt>Files</dt><dd>{a.files.length} verified</dd>
              <dt>Manifest</dt><dd className="lab-id">{a.manifest_sha256}</dd>
              <dt>Verified</dt><dd>{a.verified_at}</dd>
              <dt>Profile</dt><dd className="lab-id">{a.compatibility.profile ?? "Not stated"}</dd>
            </dl>
            {a.compatibility.supported ? (
              <>
                <p className={s.muted}>The serving revision pins this artifact to the supported profile above. It starts no engine and reserves no hardware.</p>
                <SetupForm projectId={project.project_id} artifactId={a.artifact_id} idempotencyKey={randomUUID()} />
              </>
            ) : (
              <div role="alert">
                <p>This artifact cannot be served with a supported profile:</p>
                <ul>{reasons.map((r) => <li key={`${r.field} ${r.code}`}>{r.field}: {r.message}</li>)}</ul>
              </div>
            )}
          </section>
        );
      }
    } else {
      const revisions = await api.call(workspace, "get", "/lab/v1/control/model-projects/{project_id}/revisions", { params: { project_id: project.project_id }, query: { limit: 100 } });
      const revision = revisions.ok ? revisions.data.data.find((r) => r.serving_version_id === q.revision) : undefined;
      body = !revisions.ok ? unread(revisions, "this serving revision") : revision === undefined ? (
        <ServiceState state="not_found" title="That serving revision is not in this project" explanation="It may belong to another project or workspace." action={back(`/models/new?project=${encodeURIComponent(project.project_id)}`, "Back to the project")} />
      ) : (
        <>
          <section className={s.section} aria-labelledby="revision">
            <h2 id="revision">Serving revision</h2>
            <dl className={s.facts}>
              <dt>Model</dt><dd className="lab-id">{revision.public_model_id}</dd>
              <dt>Revision</dt><dd>{revision.revision_label}</dd>
              <dt>Serving version</dt><dd className="lab-id">{revision.serving_version_id}</dd>
              <dt>Artifact</dt><dd className="lab-id">{revision.artifact_id}</dd>
            </dl>
          </section>
          <ServiceState
            state="unavailable"
            title="Serving readiness can't be verified here yet"
            explanation="A private deployment, and its check on a real engine, need the hosting service, which this Lab deployment does not offer yet. This revision is recorded; it is not serving."
            action={back("/models", "View models")}
          />
        </>
      );
    }
  }
  return (
    <>
      {header}
      <Steps current={current} />
      {body}
    </>
  );
}
