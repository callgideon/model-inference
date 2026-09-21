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
import {
  firstCursorState,
  hasPreviousPage,
  ledgerHref,
  nextCursorState,
  pageNumberOf,
  parsePageCursor,
  previousCursorState,
  viewStateOf,
} from "../usage/view-model";
import { PromotionalBalanceCard } from "./balance-card";
import { ledgerRowView } from "./view-model";

export const metadata = { title: "Balance · infrx" };

export default async function BillingPage({ searchParams }: PageProps<"/billing">) {
  const params = await searchParams;
  const { services, session } = consoleContext();
  const page = parsePageCursor(params);

  const [balance, ledger] = await Promise.all([
    services.balances(session),
    services.ledger(session, { limit: 25, ...(page.cursor === null ? {} : { cursor: page.cursor }) }),
  ]);

  const walletState = viewStateOf(balance, () => false);
  const ledgerState = viewStateOf(ledger, (value) => value.items.length === 0);
  const here = ledgerHref(page);
  const firstPageHref = ledgerHref(firstCursorState(page));

  return (
    <>
      <PageHeader
        title="Balance"
        subtitle="The free pilot runs on promotional credit granted by the infrx team. There is nothing to pay."
      />

      {walletState.kind === "ready" ? (
        <PromotionalBalanceCard
          balance={walletState.value}
          hasHistory={ledger.ok && (ledger.value.items.length > 0 || page.cursor !== null)}
        />
      ) : null}
      {walletState.kind === "error" ? (
        <ErrorPanel
          title="Your balance could not be loaded"
          message={walletState.message}
          code={walletState.code}
          recovery={walletState.recovery}
          href={here}
          firstPageHref={firstPageHref}
        />
      ) : null}

      <Card className="mt-4">
        <CardHeader className="grid-cols-[1fr_auto] items-center">
          <CardTitle>Ledger</CardTitle>
          <Badge variant="outline">Newest first</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {ledgerState.kind === "error" ? (
            <div className="p-4">
              <ErrorPanel
                title="The ledger could not be loaded"
                message={ledgerState.message}
                code={ledgerState.code}
                recovery={ledgerState.recovery}
                href={here}
                firstPageHref={firstPageHref}
              />
            </div>
          ) : null}

          {ledgerState.kind === "empty" ? (
            <div className="p-4">
              <EmptyPanel>
                Nothing on the ledger yet. Grants, adjustments and settled requests appear here as
                they happen.
              </EmptyPanel>
            </div>
          ) : null}

          {ledgerState.kind === "ready" ? (
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
                {ledgerState.value.items.map(ledgerRowView).map((row) => (
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

      {ledgerState.kind === "ready" ? (
        <Pager
          label="Ledger pages"
          page={pageNumberOf(page)}
          firstHref={firstPageHref}
          previousHref={hasPreviousPage(page) ? ledgerHref(previousCursorState(page)) : null}
          nextHref={
            ledgerState.value.next_cursor === null
              ? null
              : ledgerHref(nextCursorState(page, ledgerState.value.next_cursor))
          }
        />
      ) : null}
    </>
  );
}
