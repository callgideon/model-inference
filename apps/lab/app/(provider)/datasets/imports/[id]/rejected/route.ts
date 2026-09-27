// N4: an import job's rejected rows as a JSONL download (line, reason, detail; never the row itself).
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { datasetsPort } from "@/lib/services/datasets/server";
import { rejectedRowsJsonl } from "@/lib/services/datasets/views";
import { failureStatus } from "@/lib/services/datasets/port";

export async function GET(_: Request, { params }: { params: Promise<{ id: string }> }) {
  const workspace = await requireProviderWorkspace();
  const id = (await params).id;
  const job = await (await datasetsPort()).importJob(workspace.providerId, id);
  if (!job.ok) return new Response(job.detail, { status: failureStatus(job) });
  return new Response(rejectedRowsJsonl(job.value), {
    headers: { "content-type": "application/x-ndjson", "content-disposition": `attachment; filename="rejected-${id}.jsonl"` },
  });
}
