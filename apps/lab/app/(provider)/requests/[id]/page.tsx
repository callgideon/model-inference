import { requireProviderWorkspace } from "@/lib/auth/guard";
import { reviewRequestFeedback } from "@/lib/services/review/actions";
import { ContentPanel, FeedbackPanel, MetadataPanel } from "@/components/traces/detail/panels";
import { tracePorts } from "@/components/traces/detail/port";
import { TRACE_COPY } from "@/components/traces/detail/view";
import { JudgePanel } from "@/components/traces/judge/panel";
import { judgePort } from "@/components/traces/judge/port";

export const metadata = { title: "Request · infrx Lab" };

// V2: one request on the provider's own deployments. The trace read and C3F's feedback are read
// independently, so feedback accepted before the projection lands still shows (FEEDBACK-ACK). Content
// reads (C2, WR-V2-2) are not wired: the panel states the record's content state. V3: the judge runs.
export default async function RequestDetail({ params }: PageProps<"/requests/[id]">) {
  const workspace = await requireProviderWorkspace();
  const { id } = await params;
  const { traces } = tracePorts();
  const [trace, feedback, judge] = await Promise.all([traces.detail(workspace, id), reviewRequestFeedback(id), judgePort().runs(workspace, id)]);
  return (
    <>
      <h1>Request</h1>
      {!trace.ok && <p role="alert">{TRACE_COPY[trace.reason]}</p>}
      {trace.ok && <MetadataPanel detail={trace.value} />}
      {trace.ok && <ContentPanel detail={trace.value} />}
      <FeedbackPanel result={feedback} />
      {trace.ok && <JudgePanel result={judge} />}
    </>
  );
}
