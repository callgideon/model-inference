// N4: one import job. Long imports run on the backend; this page refreshes while one runs and shows
// success only for a published job.
import { notFound } from "next/navigation";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { datasetsPort } from "@/lib/services/datasets/server";
import { FAILURE_COPY, importView } from "@/lib/services/datasets/views";

export default async function ImportJob({ params }: { params: Promise<{ id: string }> }) {
  const workspace = await requireProviderWorkspace();
  const id = (await params).id;
  const job = await (await datasetsPort()).importJob(workspace.providerId, id);
  if (!job.ok && job.error === "not_found") notFound();
  if (!job.ok) return <p role="alert">{FAILURE_COPY[job.error]}</p>;
  const view = importView(job.value);
  return (
    <>
      {view.poll && <meta httpEquiv="refresh" content="5" />}
      <h1>{view.title}</h1>
      <p role={view.tone === "error" ? "alert" : "status"}>{view.detail}</p>
      {(job.value.report?.rejected.length ?? 0) > 0 && (
        <p>
          <a href={`/datasets/imports/${encodeURIComponent(id)}/rejected`} download>
            Download the rejected rows (JSONL)
          </a>
        </p>
      )}
      {view.tone === "success" && job.value.report?.datasetRef && (
        <p>
          <a href={`/datasets/${encodeURIComponent(job.value.report.datasetRef)}`}>Inspect the version</a>
        </p>
      )}
    </>
  );
}
