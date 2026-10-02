import { requireProviderWorkspace } from "@/lib/auth/guard";
import { PageHeader } from "@/components/ui/page-header";
import { tracePorts } from "@/components/traces/detail/port";
import { parseListParams } from "@/components/traces/list/query";
import { FilterNote, RejectedParams, RequestTable } from "@/components/traces/list/trace-table";
import { buildListView } from "@/components/traces/list/view-model";

export const metadata = { title: "Requests · infrx Lab" };

// UX-05 (L-05): the requests on this workspace's own deployments, newest first, read from lab-api's
// provider trace read as the signed-in user. Metadata for every row; content only on a request's own
// page and only where its organization shares it. The filter and cursor are the route's.
export default async function Requests({ searchParams }: PageProps<"/requests">) {
  const workspace = await requireProviderWorkspace();
  const params = parseListParams(await searchParams);
  const { traces } = tracePorts();
  const view = buildListView(await traces.list(workspace, params.cursor, params.filter), params.cursor, params.filter);
  return (
    <div className="lab-stack">
      <PageHeader title="Requests" purpose="Requests on this workspace's deployments, newest first. Open one to see its record, content where shared, feedback and judge runs." />
      <RejectedParams rejected={params.rejected} ignored={params.ignored} />
      <FilterNote filter={params.filter} />
      <RequestTable view={view} />
    </div>
  );
}
