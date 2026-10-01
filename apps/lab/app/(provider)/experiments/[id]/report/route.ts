// B4.c reproducibility export: the stored B2 report, verbatim (it names both runs, the case universe
// and the protocol by digest and carries its own), for the session's workspace only.
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { evaluationPort } from "@/lib/services/evaluation/port";

const HEADERS = { "cache-control": "private, no-store" };

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }): Promise<Response> {
  const workspace = await requireProviderWorkspace();
  const { id } = await params;
  const list = await evaluationPort().experiments(workspace);
  if (!list.ok) return Response.json({ refusal: list.reason }, { status: 503, headers: HEADERS });
  const experiment = list.value.find((e) => e.experiment_id === id);
  if (experiment === undefined) return Response.json({ refusal: "not_found" }, { status: 404, headers: HEADERS });
  if (experiment.report === null) return Response.json({ refusal: "conflict" }, { status: 409, headers: HEADERS });
  return Response.json(experiment.report, {
    headers: { ...HEADERS, "content-disposition": `attachment; filename="experiment-${experiment.experiment_id}.json"` },
  });
}
