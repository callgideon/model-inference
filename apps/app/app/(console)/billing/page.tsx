import { ConsoleDataUnavailable, ConsolePreviewNotice } from "@/components/console-data-state";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { consoleContext } from "../usage/fake-console-context";
import { EmptyPanel, ErrorPanel, Pager } from "../usage/states";
import { parsePageCursor } from "../usage/view-model";
import { PromotionalBalanceCard } from "./balance-card";
import { billingPageModel, ledgerPageQuery } from "./view-model";

export const metadata = { title: "Balance · infrx" };

export default async function BillingPage({ searchParams }: PageProps<"/billing">) {
  const params = await searchParams;
  const context = consoleContext();
  if (context === null) return <ConsoleDataUnavailable title="Balance" />;
  const { services, session } = context;
  const state = parsePageCursor(params);

  const [balance, ledger] = await Promise.all([
    services.balances(session),
    services.ledger(session, ledgerPageQuery(state)),
  ]);

  const model = billingPageModel({ state, balance, ledger });

  return (
    <>
      <ConsolePreviewNotice />
      <PageHeader
        title="Balance"
        subtitle="Example balances and ledger entries from the legacy USD pilot."
      />

      {model.balance.kind === "ready" ? <PromotionalBalanceCard model={model.balance.value} /> : null}
      {model.balance.kind === "error" ? (
        <ErrorPanel
          title="Your balance could not be loaded"
          state={model.balance}
          href={model.here}
          firstPageHref={model.firstHref}
        />
      ) : null}

      <Card className="mt-4">
        <CardHeader className="grid-cols-[1fr_auto] items-center">
          <CardTitle>Ledger</CardTitle>
          <Badge variant="outline">Newest first</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {model.ledger.kind === "error" ? (
            <div className="p-4">
              <ErrorPanel
                title="The ledger could not be loaded"
                state={model.ledger}
                href={model.here}
                firstPageHref={model.firstHref}
              />
            </div>
          ) : null}

          {model.ledger.kind === "empty" ? (
            <div className="p-4">
              <EmptyPanel>
                Nothing on the ledger yet. Grants, adjustments and settled requests appear here as
                they happen.
              </EmptyPanel>
            </div>
          ) : null}

          {model.ledger.kind === "ready" ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When (UTC)</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>By</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {model.ledger.value.rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="whitespace-nowrap text-muted-foreground">
                      {row.when}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{row.kind}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{row.reason}</TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground">
                      {row.actor}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.amount}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>

      {model.ledger.kind === "ready" ? (
        <Pager
          label="Ledger pages"
          page={model.ledger.value.page}
          firstHref={model.ledger.value.firstHref}
          previousHref={model.ledger.value.previousHref}
          nextHref={model.ledger.value.nextHref}
        />
      ) : null}
    </>
  );
}
