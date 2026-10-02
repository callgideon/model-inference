import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { JobRowView } from "./credit-view-model";

/**
 * The Usage request list (UX-07, C-04): request, start, model, then the execution state and the money
 * state in separate columns. A charge appears only once settled; a hold is shown as held. Markup only:
 * every value is `jobRowView`'s.
 */
export function RequestsTable({ rows }: { rows: JobRowView[] }) {
  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Request</TableHead>
              <TableHead>Started (UTC)</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Charge</TableHead>
              <TableHead className="text-right">Charged</TableHead>
              <TableHead className="text-right">Held</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.requestId}>
                <TableCell>
                  <Link className="font-mono text-xs underline underline-offset-4" href={row.detailHref} title={row.requestId}>
                    {row.requestId.slice(0, 8)}…<span className="sr-only"> details for request {row.requestId}</span>
                  </Link>
                </TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">{row.when}</TableCell>
                <TableCell className="whitespace-nowrap">
                  <span title={row.revision}>{row.model}</span>
                  <span className="block text-xs text-muted-foreground">{row.mode}</span>
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{row.status}</Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={row.charge.tone === "warning" ? "destructive" : "outline"}>{row.charge.label}</Badge>
                  <span className="mt-0.5 block max-w-64 text-xs whitespace-normal text-muted-foreground">{row.charge.detail}</span>
                </TableCell>
                <TableCell className="text-right tabular-nums">{row.charge.amount ?? "—"}</TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">{row.charge.held ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
