// N4: one export part, read through the backend (which re-checks the training grant and the tombstones
// at every read). The browser gets the items, never a storage URL or credential.
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { datasetsPort } from "@/lib/services/datasets/server";
import { failureStatus } from "@/lib/services/datasets/port";

export async function GET(_: Request, { params }: { params: Promise<{ id: string; part: string }> }) {
  const workspace = await requireProviderWorkspace();
  const { id, part } = await params;
  if (!/^\d{1,6}$/.test(part)) return new Response("no such part", { status: 404 });
  const data = await (await datasetsPort()).readPart(workspace.providerId, id, Number(part));
  if (!data.ok) return new Response(data.detail, { status: failureStatus(data) });
  return new Response(data.value, {
    headers: { "content-type": "application/x-ndjson", "content-disposition": `attachment; filename="export-${id}-${part}.jsonl"` },
  });
}
