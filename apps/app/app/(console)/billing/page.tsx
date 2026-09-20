import { CreditsCard } from "@/components/credits-card";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getCredits } from "@/lib/credits";
import { dateTime, money } from "@/lib/format";
import { getSession } from "@/lib/session";

export const metadata = { title: "Billing · infrx" };

const SOON = "Coming soon — payments are not wired up yet";

export default async function BillingPage() {
  const session = await getSession();
  const credits = await getCredits(session.orgId);

  return (
    <>
      <PageHeader title="Billing" subtitle="Prepaid credits. Usage is drawn down per request." />

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <CreditsCard credits={credits} />

        <Card>
          <CardHeader>
            <CardTitle>Payment</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <div>
                <div>Card</div>
                <div className="text-xs text-muted-foreground">No card on file</div>
              </div>
              <Button variant="outline" size="sm" disabled title={SOON}>
                Add card
              </Button>
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <div>Auto reload</div>
                <div className="text-xs text-muted-foreground">
                  Top up automatically when the balance runs low
                </div>
              </div>
              <Badge variant="secondary">Off</Badge>
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <div>Add credits</div>
                <div className="text-xs text-muted-foreground">
                  Ask an operator for a grant while payments are being built
                </div>
              </div>
              <Button variant="outline" size="sm" disabled title={SOON}>
                Add credits
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Invoices</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="py-8 text-center text-sm text-muted-foreground">
            No invoices yet. Credits are granted manually until payments ship.
          </p>
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Ledger</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Reason</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {credits.rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="text-muted-foreground">{dateTime(r.created_at)}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{r.kind}</Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{r.reason ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {money(Number(r.delta_usd))}
                  </TableCell>
                </TableRow>
              ))}
              {credits.rows.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="py-10 text-center text-sm text-muted-foreground"
                  >
                    Nothing on the ledger yet.
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  );
}
