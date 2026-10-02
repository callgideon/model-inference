import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyPanel, ErrorPanel } from "../states";
import { CopyButton } from "./copy-button";
import { PHASE_LABELS, RETRY_GUIDANCE, type RequestDetailModel } from "./request-view-model";
import { ResultPanel } from "./result-panel";

/**
 * The request detail's body (UX-07, C-04): the request and its status, then its result, then usage and
 * charge, then technical details. Markup only; every state and sentence is `requestDetailModel`'s.
 * `checkedAt` is when the server read this status (the page re-renders on every poll).
 */
export function RequestDetailView({ model, here, checkedAt }: { model: RequestDetailModel; here: string; checkedAt: string }) {
  if (model.kind === "error") {
    return <ErrorPanel title="This request could not be loaded" state={model} href={here} firstPageHref="/usage" />;
  }
  if (model.kind === "empty") {
    return <EmptyPanel>We could not find this request in your account. Open it from Usage, or check the link.</EmptyPanel>;
  }
  if (model.kind !== "ready") return null;
  const detail = model.value;
  const { charge, tokens, result, failure } = detail;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="grid-cols-[1fr_auto] items-center">
          <CardTitle className="break-all font-mono text-sm">{detail.requestId}</CardTitle>
          <Badge variant={detail.phase === "finished" ? "secondary" : "outline"}>{PHASE_LABELS[detail.phase]}</Badge>
        </CardHeader>
        <CardContent className="space-y-3">
          <dl className="grid gap-4 text-sm sm:grid-cols-3">
            <Item label="Status">{detail.status}</Item>
            <Item label="Model">
              <span className="break-all">{detail.model}</span>
            </Item>
            <Item label="Submitted (UTC)">{detail.created}</Item>
          </dl>
          {failure !== null ? (
            <div className="rounded-md border p-3 text-sm">
              <p className="font-medium">{failure.title}</p>
              <p className="mt-1 text-muted-foreground">{failure.action}</p>
            </div>
          ) : null}
          <CopyButton text={detail.requestId} label="Copy request ID" />
          {detail.poll ? (
            <p className="text-xs text-muted-foreground">Status last checked {checkedAt.slice(11, 19)} UTC</p>
          ) : null}
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

      <Card>
        <CardHeader className="grid-cols-[1fr_auto] items-center">
          <CardTitle>Usage and charge</CardTitle>
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
          <CardTitle>Technical details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <dl className="grid gap-4 text-sm sm:grid-cols-3">
            <Item label="Request ID">
              <span className="break-all font-mono text-xs">{detail.requestId}</span>
            </Item>
            <Item label="Serving revision">
              <span className="break-all">{detail.revision}</span>
            </Item>
            <Item label="Mode">{detail.mode}</Item>
          </dl>
          <p className="text-xs text-muted-foreground">{RETRY_GUIDANCE}</p>
        </CardContent>
      </Card>
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
