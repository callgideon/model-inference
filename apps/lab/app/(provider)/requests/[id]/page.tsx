import { requireProviderWorkspace } from "@/lib/auth/guard";
import { reviewRequestFeedback } from "@/lib/services/review/actions";
import { PageHeader } from "@/components/ui/page-header";
import { ContentPanel, FeedbackPanel, MetadataPanel, TraceRefused } from "@/components/traces/detail/panels";
import { tracePorts } from "@/components/traces/detail/port";
import { JudgePanel } from "@/components/traces/judge/panel";
import { judgePort } from "@/components/traces/judge/port";

export const metadata = { title: "Request · infrx Lab" };

// UX-05 (L-05 detail): breadcrumb, summary, the authorized content region, then feedback and judge
// runs. The trace read (content inline when granted) and feedback are read independently, so feedback
// accepted before the projection lands still shows (FEEDBACK-ACK); judge runs show only beside a request
// the trace read returned.
export default async function RequestDetail({ params }: PageProps<"/requests/[id]">) {
  const workspace = await requireProviderWorkspace();
  const { id } = await params;
  const { traces } = tracePorts();
  const [trace, feedback, judge] = await Promise.all([traces.detail(workspace, id), reviewRequestFeedback(id), judgePort().runs(workspace, id)]);
  return (
    <div className="lab-stack">
      <PageHeader title="Request" breadcrumb={[{ href: "/requests", label: "Requests" }]} />
      {!trace.ok && <TraceRefused reason={trace.reason} retryHref={`/requests/${encodeURIComponent(id)}`} />}
      {trace.ok && <MetadataPanel detail={trace.value} />}
      {trace.ok && <ContentPanel detail={trace.value} />}
      <FeedbackPanel result={feedback} />
      {trace.ok && <JudgePanel result={judge} />}
    </div>
  );
}
