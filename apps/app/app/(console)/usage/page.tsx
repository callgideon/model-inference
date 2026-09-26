import Link from "next/link";
import { ConsolePreviewNotice } from "@/components/console-data-state";
import { PageHeader } from "@/components/page-header";
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
import { CreditBalanceCard } from "../billing/credit-card";
import { consumerCreditReads } from "../billing/credit-context";
import { creditCardState } from "../billing/credit-view-model";
import { jobsPageModel, jobsPageRequest, parseJobFilters, type JobRowView } from "./credit-view-model";
import { EmptyPanel, ErrorPanel, Pager } from "./states";
import { UsageControls } from "./usage-controls";

export const metadata = { title: "Usage · infrx" };

export default async function UsagePage({ searchParams }: PageProps<"/usage">) {
  const params = await searchParams;
  const { reads, preview, now } = await consumerCreditReads();
  const filters = parseJobFilters(params);

  const [wallet, jobs] = await Promise.all([reads.wallet(), reads.jobs(jobsPageRequest(filters, now))]);
  const found = wallet.ok ? wallet.value : null;
  const [creditsIn, keys] =
    found === null ? [null, null] : await Promise.all([reads.creditsIn(found.walletId), reads.keys(found.orgId)]);
  const card = creditCardState(wallet, creditsIn);
  const model = jobsPageModel({ filters, jobs });

  return (
    <>
      {preview ? <ConsolePreviewNotice /> : null}
      <PageHeader
        title="Usage"
        subtitle="Your requests, newest first, with what each one charged or holds."
        action={<UsageControls filters={model.filters} keys={keys?.ok ? keys.value : []} />}
      />

      {card.kind === "ready" ? <CreditBalanceCard model={card.value} /> : null}
      {card.kind === "error" ? (
        <ErrorPanel
          title="Your credits could not be loaded"
          state={card}
          href={model.here}
          firstPageHref={model.firstHref}
        />
      ) : null}

      <h2 className="mt-8 mb-3 font-heading text-base font-medium">Requests</h2>

      {model.rows.kind === "error" ? (
        <ErrorPanel
          title="Your requests could not be loaded"
          state={model.rows}
          href={model.here}
          firstPageHref={model.firstHref}
        />
      ) : null}

      {model.rows.kind === "empty" ? <EmptyPanel>{model.emptyText}</EmptyPanel> : null}

      {model.rows.kind === "ready" ? (
        <>
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When (UTC)</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Input</TableHead>
                    <TableHead className="text-right">Output</TableHead>
                    <TableHead className="text-right">Charged</TableHead>
                    <TableHead className="text-right">Held</TableHead>
                    <TableHead>
                      <span className="sr-only">Request</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {model.rows.value.rows.map((row) => (
                    <JobRow key={row.requestId} row={row} />
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Pager
            label="Usage pages"
            page={model.rows.value.page}
            firstHref={model.rows.value.firstHref}
            previousHref={model.rows.value.previousHref}
            nextHref={model.rows.value.nextHref}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            <strong>Charged</strong> is what settlement took from your balance, in the unit shown.{" "}
            <strong>Held</strong> is reserved while a request runs or awaits reconciliation — it is
            not a charge, and usage that was not reported is never estimated into one.
          </p>
        </>
      ) : null}
    </>
  );
}

function JobRow({ row }: { row: JobRowView }) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap text-muted-foreground">{row.when}</TableCell>
      <TableCell className="whitespace-nowrap">
        <span title={row.revision}>{row.model}</span>
        <span className="block text-xs text-muted-foreground">{row.mode}</span>
      </TableCell>
      <TableCell>
        <div className="flex flex-col gap-0.5">
          <span className="flex items-center gap-2">
            <Badge variant={row.charge.tone === "warning" ? "destructive" : "outline"}>{row.charge.label}</Badge>
            <span className="text-xs text-muted-foreground">{row.status}</span>
          </span>
          <span className="text-xs text-muted-foreground">{row.charge.detail}</span>
        </div>
      </TableCell>
      <TableCell className="text-right tabular-nums" title={row.tokens.note ?? undefined}>
        {row.tokens.input}
      </TableCell>
      <TableCell className="text-right tabular-nums" title={row.tokens.note ?? undefined}>
        {row.tokens.output}
      </TableCell>
      <TableCell className="text-right tabular-nums">{row.charge.amount ?? "—"}</TableCell>
      <TableCell className="text-right tabular-nums text-muted-foreground">{row.charge.held ?? "—"}</TableCell>
      <TableCell>
        <Link className="text-sm underline underline-offset-4" href={row.detailHref}>
          Details<span className="sr-only"> for request {row.requestId}</span>
        </Link>
      </TableCell>
    </TableRow>
  );
}
