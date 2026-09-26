import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsolePreviewNotice } from "@/components/console-data-state";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { requestDetailHref } from "../../billing/credit-view-model";
import { EmptyPanel, ErrorPanel } from "../states";
import { consumerRequestReads } from "./request-context";
import { PHASE_LABELS, RETRY_GUIDANCE, pollsFor, requestDetailModel, type RequestDetail } from "./request-view-model";
import { ResultPanel } from "./result-panel";
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

      {model.kind === "error" ? (
        <ErrorPanel title="This request could not be loaded" state={model} href={here} firstPageHref="/usage" />
      ) : null}
      {model.kind === "empty" ? (
        <EmptyPanel>
          We could not find this request in your account. Open it from Usage, or check the link.
        </EmptyPanel>
      ) : null}
      {model.kind === "ready" ? <Detail detail={model.value} /> : null}

      {pollsFor(model) ? <StatusPoller /> : null}
    </>
  );
}

function Detail({ detail }: { detail: RequestDetail }) {
  const { charge, tokens, result, failure } = detail;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="grid-cols-[1fr_auto] items-center">
          <CardTitle>Status</CardTitle>
          <Badge variant={detail.phase === "finished" ? "secondary" : "outline"}>{PHASE_LABELS[detail.phase]}</Badge>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Item label="Status">{detail.status}</Item>
            <Item label="Submitted (UTC)">{detail.created}</Item>
            <Item label="Model">
              <span className="break-all">{detail.model}</span>
              <span className="block text-xs text-muted-foreground break-all">{detail.revision}</span>
            </Item>
            <Item label="Mode">{detail.mode}</Item>
          </dl>
          {failure !== null ? (
            <div className="mt-4 rounded-md border p-3 text-sm">
              <p className="font-medium">{failure.title}</p>
              <p className="mt-1 text-muted-foreground">{failure.action}</p>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="grid-cols-[1fr_auto] items-center">
          <CardTitle>Charge</CardTitle>
          <Badge variant={charge.tone === "warning" ? "destructive" : "outline"}>{charge.label}</Badge>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Item label="Charged">{charge.amount ?? "—"}</Item>
            <Item label="Held">{charge.held ?? "—"}</Item>
            <Item label="Input tokens">{tokens.input}</Item>
            <Item label="Output tokens">{tokens.output}</Item>
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            {charge.detail} Amounts are in {detail.unit}.
            {tokens.note === null ? null : ` ${tokens.note}`}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Result</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">{result.note}</p>
          {result.access === "available" && result.expiresAt !== null ? (
            <ResultPanel requestId={detail.requestId} expiresAt={result.expiresAt} />
          ) : null}
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground">{RETRY_GUIDANCE}</p>
    </div>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 tabular-nums">{children}</dd>
    </div>
  );
}
