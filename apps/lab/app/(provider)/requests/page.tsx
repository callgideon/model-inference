import { requireProviderWorkspace } from "@/lib/auth/guard";
import { labTraces } from "@/lib/services/traces/server";
import { parseListParams } from "@/components/traces/list/query";
import { RejectedParams, RequestTable } from "@/components/traces/list/trace-table";
import { buildListView } from "@/components/traces/list/view-model";

export const metadata = { title: "Requests · infrx Lab" };

// V1M: the requests on this workspace's own deployments, newest first, read from lab-api's provider trace
// read (R176) as the signed-in user: metadata for every row, content only where the request's organization
// shares it (shown on the request's own page, V2). Moved from the App's /traces (V1).
export default async function Requests({ searchParams }: PageProps<"/requests">) {
  const workspace = await requireProviderWorkspace();
  const params = parseListParams(await searchParams);
  const view = buildListView(await labTraces().list(workspace, params.cursor), params.cursor);
  return (
    <>
      <h1>Requests</h1>
      <p>Requests on this workspace&apos;s deployments, newest first. Open one to see its record, content where shared, feedback and judge runs.</p>
      <RejectedParams rejected={params.rejected} ignored={params.ignored} />
      <RequestTable view={view} />
    </>
  );
}
