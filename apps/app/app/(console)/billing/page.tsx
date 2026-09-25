import Link from "next/link";
import { ConsolePreviewNotice } from "@/components/console-data-state";
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
import { EmptyPanel, ErrorPanel, Pager } from "../usage/states";
import { parsePageCursor } from "../usage/view-model";
import { CreditBalanceCard } from "./credit-card";
import { consumerCreditReads } from "./credit-context";
import { LEDGER_PAGE_SIZE, creditsPageModel } from "./credit-view-model";

export const metadata = { title: "Credits · infrx" };

export default async function CreditsPage({ searchParams }: PageProps<"/billing">) {
  const params = await searchParams;
  const { reads, preview } = await consumerCreditReads();
  const state = parsePageCursor(params);

  const wallet = await reads.wallet();
  const found = wallet.ok ? wallet.value : null;
  const [creditsIn, ledger, legacy] =
    found === null
      ? [null, null, null]
      : await Promise.all([
          reads.creditsIn(found.walletId),
          reads.ledger(found.walletId, { limit: LEDGER_PAGE_SIZE, cursor: state.cursor }),
          reads.legacyUsd(found.orgId),
        ]);

  const model = creditsPageModel({ state, wallet, creditsIn, ledger, legacy });

  return (
    <>
      {preview ? <ConsolePreviewNotice /> : null}
      <PageHeader title="Credits" subtitle="Your CREDIT balance, its ledger and any legacy USD history." />

      {model.card.kind === "ready" ? <CreditBalanceCard model={model.card.value} /> : null}
      {model.card.kind === "error" ? (
        <ErrorPanel
          title="Your credits could not be loaded"
          state={model.card}
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
                Nothing on the ledger yet. Your signup grant, adjustments and request charges appear
                here as they happen.
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
                  <TableHead className="text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {model.ledger.value.rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="whitespace-nowrap text-muted-foreground">{row.when}</TableCell>
                    <TableCell>
                      {row.detailHref === null ? (
                        <Badge variant="outline">{row.kind}</Badge>
                      ) : (
                        <Link className="underline underline-offset-4" href={row.detailHref}>
                          {row.kind}
                        </Link>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{row.reason}</TableCell>
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

      {model.legacy.kind === "ready" ? (
        <Card className="mt-6">
          <CardHeader className="grid-cols-[1fr_auto] items-center">
            <CardTitle>Legacy USD statement</CardTitle>
            <Badge variant="outline">USD</Badge>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="font-heading text-lg font-semibold tabular-nums">{model.legacy.value.balance}</p>
            <p className="text-xs text-muted-foreground">
              {model.legacy.value.entries} historical {model.legacy.value.entries === 1 ? "entry" : "entries"}
            </p>
            <p className="text-sm text-muted-foreground">{model.legacy.value.text}</p>
            {model.legacy.value.hold === null ? null : <p className="text-sm">{model.legacy.value.hold}</p>}
          </CardContent>
        </Card>
      ) : null}
      {model.legacy.kind === "error" ? (
        <div className="mt-6">
          <ErrorPanel
            title="Your legacy USD history could not be loaded"
            state={model.legacy}
            href={model.here}
            firstPageHref={model.firstHref}
          />
        </div>
      ) : null}
    </>
  );
}
