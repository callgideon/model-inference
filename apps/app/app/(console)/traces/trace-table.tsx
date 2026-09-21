import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { dateTime, ms, num } from "@/lib/format";
import { displayMoney } from "../../../lib/contracts/money.ts";
import type { RejectedParam } from "./query.ts";
import type { ContentTone, StatusTone, TraceListIndicators, TraceListView, TraceRowView } from "./view-model.ts";

const STATUS_VARIANT: Record<StatusTone, "default" | "secondary" | "destructive" | "outline"> = {
  ok: "secondary",
  muted: "outline",
  warn: "default",
  error: "destructive",
};

const CONTENT_VARIANT: Record<ContentTone, "default" | "secondary" | "destructive" | "outline"> = {
  ok: "secondary",
  muted: "outline",
  waiting: "outline",
  warn: "destructive",
};

/** Filters the URL asked for and this page refused, so a wrong link is visible rather than silent. */
export function RejectedFilters({
  rejected,
  ignored,
}: {
  rejected: RejectedParam[];
  ignored: string[];
}) {
  if (rejected.length === 0 && ignored.length === 0) return null;
  return (
    <div
      role="status"
      className="mb-4 rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground"
    >
      {rejected.map((item) => (
        <p key={item.name}>
          <span className="font-medium text-foreground">{item.name}</span> was ignored: {item.why}.
        </p>
      ))}
      {ignored.length > 0 ? (
        <p>
          The trace list does not use{" "}
          <span className="font-medium text-foreground">{ignored.join(", ")}</span>, so it had no
          effect.
        </p>
      ) : null}
    </div>
  );
}

/**
 * What this page of rows says about capture health. Deliberately page-scoped and worded that way:
 * an organization-wide figure would need an aggregate the console contract does not expose.
 */
function Indicators({ indicators }: { indicators: TraceListIndicators }) {
  const notes: string[] = [];
  if (indicators.pending > 0) {
    notes.push(`${num(indicators.pending)} still being projected — content appears shortly`);
  }
  if (indicators.lost > 0) {
    const reasons = indicators.lostReasons
      .map((entry) => `${entry.reason.replace(/_/g, " ")} ×${entry.count}`)
      .join(", ");
    notes.push(`${num(indicators.lost)} lost their content (${reasons})`);
  }
  if (indicators.metadataOnly > 0) {
    notes.push(`${num(indicators.metadataOnly)} captured metadata only, by their key's setting`);
  }
  if (indicators.inFlight > 0) {
    notes.push(`${num(indicators.inFlight)} not finished, so tokens and cost are not final`);
  }
  if (indicators.unexpected > 0) {
    notes.push(
      `${num(indicators.unexpected)} report tracing off, which a trace row cannot — the projection is inconsistent`,
    );
  }
  if (notes.length === 0) return null;
  return (
    <p className="mb-3 text-sm text-muted-foreground">On this page: {notes.join(" · ")}.</p>
  );
}

function Row({ row }: { row: TraceRowView }) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap">
        {/* The row's link, so the list is walkable with Tab and openable with Enter. */}
        <Link
          href={row.href}
          className="rounded-sm underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
          aria-label={`Trace ${row.requestId} from ${dateTime(row.createdAt)}`}
        >
          {dateTime(row.createdAt)}
        </Link>
      </TableCell>
      <TableCell className="max-w-40 truncate" title={row.keyLabel}>
        {row.keyLabel}
        {row.keyRevoked ? <span className="text-muted-foreground"> (revoked)</span> : null}
      </TableCell>
      <TableCell className="max-w-52 truncate" title={row.model}>
        {row.model}
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <Badge variant={STATUS_VARIANT[row.statusTone]}>{row.httpStatus}</Badge>{" "}
        <span className="text-muted-foreground">{row.jobState}</span>
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <Badge variant={CONTENT_VARIANT[row.content.tone]} title={row.content.detail}>
          {row.content.label}
        </Badge>
      </TableCell>
      <TableCell className="whitespace-nowrap tabular-nums">
        {row.promptTokens === null || row.completionTokens === null
          ? "—"
          : `${num(row.promptTokens)} / ${num(row.completionTokens)}`}
      </TableCell>
      <TableCell className="tabular-nums">{ms(row.ttftMs)}</TableCell>
      <TableCell className="tabular-nums">{ms(row.wallMs)}</TableCell>
      <TableCell className="tabular-nums">{displayMoney(row.cost)}</TableCell>
      <TableCell className="whitespace-nowrap tabular-nums">
        {row.feedbackCount === 0 && row.scoreCount === 0
          ? "—"
          : `${num(row.feedbackCount)} fb · ${num(row.scoreCount)} scores`}
      </TableCell>
    </TableRow>
  );
}

export function TraceTable({ view }: { view: TraceListView }) {
  if (view.kind === "error") {
    return (
      <Card>
        <CardContent role="alert" className="space-y-3 py-8 text-center">
          <p className="text-sm">{view.message}</p>
          {view.action ? (
            <Link href={view.action.href} className="text-sm text-primary underline-offset-4 hover:underline">
              {view.action.label}
            </Link>
          ) : null}
        </CardContent>
      </Card>
    );
  }

  if (view.kind === "empty") {
    return (
      <Card>
        <CardContent className="space-y-3 py-8 text-center">
          <p className="text-sm text-muted-foreground">{view.message}</p>
          {view.action ? (
            <Link href={view.action.href} className="text-sm text-primary underline-offset-4 hover:underline">
              {view.action.label}
            </Link>
          ) : null}
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Indicators indicators={view.indicators} />
      <Card>
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Time</TableHead>
                <TableHead scope="col">Key</TableHead>
                <TableHead scope="col">Model</TableHead>
                <TableHead scope="col">Outcome</TableHead>
                <TableHead scope="col">Content</TableHead>
                <TableHead scope="col">Tokens in / out</TableHead>
                <TableHead scope="col">TTFT</TableHead>
                <TableHead scope="col">Latency</TableHead>
                <TableHead scope="col">Cost</TableHead>
                <TableHead scope="col">Signals</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {view.rows.map((row) => (
                <Row key={row.requestId} row={row} />
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <div className="mt-4 flex justify-end">
        {view.nextHref === null ? (
          <p className="text-sm text-muted-foreground">End of this window.</p>
        ) : (
          <Link
            href={view.nextHref}
            className="text-sm text-primary underline-offset-4 hover:underline"
            rel="next"
          >
            Next page →
          </Link>
        )}
      </div>
    </>
  );
}
