import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsolePreviewNotice } from "@/components/console-data-state";
import { PageHeader } from "@/components/page-header";
import { requestDetailHref } from "../../billing/credit-view-model";
import { consumerRequestReads } from "./request-context";
import { RequestDetailView } from "./request-detail";
import { pollsFor, requestDetailModel } from "./request-view-model";
import { StatusPoller } from "./status-poller";

export const metadata = { title: "Request · infrx" };

/**
 * One owned request (U4): status, charge and — while its persisted expiry allows — its result. The
 * server reads only metadata; content reaches the browser only through the no-store result route,
 * so this page's payload never carries it.
 */
export default async function RequestPage({ params }: PageProps<"/usage/[requestId]">) {
  const { requestId } = await params;
  const here = requestDetailHref(requestId);
  const source = await consumerRequestReads();
  if (source === null) redirect(`/login?next=${encodeURIComponent(here)}`);
  const model = requestDetailModel(await source.reads.job(requestId));
  const back = (
    <Link className="text-sm underline underline-offset-4" href="/usage">
      Back to Usage
    </Link>
  );

  return (
    <>
      {source.preview ? <ConsolePreviewNotice /> : null}
      <PageHeader
        title="Request"
        subtitle={model.kind === "ready" ? model.value.requestId : undefined}
        action={back}
      />

      <RequestDetailView model={model} here={here} checkedAt={new Date().toISOString()} />

      {pollsFor(model) ? <StatusPoller /> : null}
    </>
  );
}
