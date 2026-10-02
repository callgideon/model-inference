// N4 + UX-06 (L-07): one import job. Long imports run on the backend; this page refreshes while one runs
// and shows success only for a published job ("published" = a private dataset version, not a release).
import Link from "next/link";
import { notFound } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { buttonClass } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { datasetsPort } from "@/lib/services/datasets/server";
import { FAILURE_COPY, importView } from "@/lib/services/datasets/views";
import { requeueImportAction } from "../../actions";
import styles from "../../datasets.module.css";
import { ImportAgain } from "../../forms";

export const metadata = { title: "Dataset import · infrx Lab" };

const CRUMBS = [{ href: "/datasets", label: "Datasets" }];
const TONE = { success: "success", progress: "info", error: "danger" } as const;

export default async function ImportJob({ params }: PageProps<"/datasets/imports/[id]">) {
  const workspace = await requireProviderWorkspace();
  const id = (await params).id;
  const job = await (await datasetsPort()).importJob(workspace.providerId, id);
  if (!job.ok && job.error === "not_found") notFound();
  if (!job.ok) {
    return (
      <div className="lab-stack">
        <PageHeader breadcrumb={CRUMBS} title="Dataset import" />
        <ServiceState
          state={job.error === "denied" ? "denied" : "unavailable"}
          title="We couldn't load this import"
          explanation={FAILURE_COPY[job.error]}
          action={<Link className={buttonClass()} href={`/datasets/imports/${encodeURIComponent(id)}`}>Try again</Link>}
        />
      </div>
    );
  }
  const view = importView(job.value);
  const report = job.value.report;
  return (
    <div className="lab-stack">
      {view.poll && <meta httpEquiv="refresh" content="5" />}
      <PageHeader breadcrumb={CRUMBS} title={view.title} purpose={<span className="lab-id">Import {id}</span>} />
      <p>
        <Badge tone={TONE[view.tone]}>{job.value.state}</Badge>
      </p>
      <p role={view.tone === "error" ? "alert" : "status"}>{view.detail}</p>
      {view.tone === "success" && <p className={styles.note}>Published means a new private dataset version exists in this workspace. It is not a public release.</p>}
      {report && (
        <dl>
          <dt>Accepted rows</dt>
          <dd>{report.accepted}</dd>
          <dt>Rejected rows</dt>
          <dd>{report.rejected.length}</dd>
          {report.sourceRef && (
            <>
              <dt>Source</dt>
              <dd className="lab-id">{report.sourceRef}</dd>
            </>
          )}
        </dl>
      )}
      {view.again && <ImportAgain importId={id} requeue={requeueImportAction} />}
      {(report?.rejected.length ?? 0) > 0 && (
        <p>
          <a href={`/datasets/imports/${encodeURIComponent(id)}/rejected`} download>
            Download the rejected rows (JSONL)
          </a>
        </p>
      )}
      {view.tone === "success" && report?.datasetRef && (
        <p>
          <Link href={`/datasets/${encodeURIComponent(report.datasetRef)}`}>Inspect the version</Link>
        </p>
      )}
    </div>
  );
}
