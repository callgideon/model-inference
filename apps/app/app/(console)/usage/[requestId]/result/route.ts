import { consumerRequestReads } from "../request-context";
import { resultResponse } from "../request-reads";

// Per user, per request, never prerendered or cached; the response itself says no-store.
export const dynamic = "force-dynamic";

/** The owned result's content, read only (U4). Nothing here submits or reruns inference. */
export async function GET(_request: Request, { params }: { params: Promise<{ requestId: string }> }) {
  const { requestId } = await params;
  const source = await consumerRequestReads();
  if (source === null) return resultResponse({ state: "signed_out" });
  return resultResponse(await source.reads.result(requestId));
}
