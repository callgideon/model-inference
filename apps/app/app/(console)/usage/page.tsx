import { PageHeader } from "@/components/page-header";
import { StatTile } from "@/components/stat-tile";
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
import { PromotionalBalanceCard } from "../billing/balance-card";
import { consoleContext } from "./fake-console-context";
import { EmptyPanel, ErrorPanel, Pager } from "./states";
import { UsageChart } from "./usage-chart";
import { UsageControls } from "./usage-controls";
import {
  dayViews,
  firstCursorState,
  hasPreviousPage,
  modelOptions,
  nextCursorState,
  pageNumberOf,
  parseUsageFilters,
  previousCursorState,
  summaryTiles,
  usageHref,
  usagePageQuery,
  usageRowView,
  usageScopeQuery,
  viewStateOf,
  type UsageRowView,
} from "./view-model";

export const metadata = { title: "Usage · infrx" };

export default async function UsagePage({ searchParams }: PageProps<"/usage">) {
  const params = await searchParams;
  const { services, session, now } = consoleContext();
  const filters = parseUsageFilters(params);
  const scope = usageScopeQuery(filters, now);

  const [rows, summary, daily, keys, balance, history] = await Promise.all([
    services.usage(session, usagePageQuery(filters, now)),
    services.usageSummary(session, scope),
    services.usageDaily(session, scope),
    services.keys.list(session),
    services.balances(session),
    services.ledger(session, { limit: 1 }),
  ]);

  const rowsState = viewStateOf(rows, (page) => page.items.length === 0);
  const summaryState = viewStateOf(summary, () => false);
  const here = usageHref(filters);
  const firstPageHref = usageHref(firstCursorState(filters));

  return (
    <>
      <PageHeader
        title="Usage"
        subtitle="Metadata only — no prompts or video are stored. Amounts are drawn from promotional pilot credit."
        action={
          <UsageControls
            filters={filters}
            keys={keys.ok ? keys.value.map((key) => ({ id: key.id, name: key.name })) : []}
            models={modelOptions(rows.ok ? rows.value.items : [], filters.model)}
          />
        }
      />

      {summaryState.kind === "ready" ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {summaryTiles(summaryState.value).map((tile) => (
            <StatTile key={tile.label} label={tile.label} value={tile.value} hint={tile.hint} />
          ))}
        </div>
      ) : null}
      {summaryState.kind === "error" ? (
        <ErrorPanel
          title="This range could not be totalled"
          message={summaryState.message}
          code={summaryState.code}
          recovery={summaryState.recovery}
          href={here}
          firstPageHref={firstPageHref}
        />
      ) : null}

      <div className="mt-6 grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Card>
          <CardContent>
            <UsageChart days={daily.ok ? dayViews(daily.value) : []} />
          </CardContent>
        </Card>
        {balance.ok ? (
          <PromotionalBalanceCard
            balance={balance.value}
            hasHistory={history.ok && history.value.items.length > 0}
          />
        ) : null}
      </div>

      <h2 className="mt-8 mb-3 font-heading text-base font-medium">Requests</h2>

      {rowsState.kind === "error" ? (
        <ErrorPanel
          title="These requests could not be loaded"
          message={rowsState.message}
          code={rowsState.code}
          recovery={rowsState.recovery}
          href={here}
          firstPageHref={firstPageHref}
        />
      ) : null}

      {rowsState.kind === "empty" ? (
        <EmptyPanel>
          No requests in this range. Widen the time range, or clear the key and model filters.
        </EmptyPanel>
      ) : null}

      {rowsState.kind === "ready" ? (
        <>
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When (UTC)</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Key</TableHead>
                    <TableHead>Outcome</TableHead>
                    <TableHead className="text-right">Input</TableHead>
                    <TableHead className="text-right">Output</TableHead>
                    <TableHead className="text-right">Charged</TableHead>
                    <TableHead className="text-right">Held</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rowsState.value.items.map(usageRowView).map((row) => (
                    <UsageTableRow key={row.requestId} row={row} />
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Pager
            label="Usage pages"
            page={pageNumberOf(filters)}
            firstHref={firstPageHref}
            previousHref={hasPreviousPage(filters) ? usageHref(previousCursorState(filters)) : null}
            nextHref={
              rowsState.value.next_cursor === null
                ? null
                : usageHref(nextCursorState(filters, rowsState.value.next_cursor))
            }
          />
          <p className="mt-2 text-xs text-muted-foreground">
            <strong>Charged</strong> is what left your balance. <strong>Held</strong> is a ceiling
            reserved while a request runs or awaits reconciliation — it is not a charge, and usage we
            cannot verify is never estimated into one.
          </p>
        </>
      ) : null}
    </>
  );
}

function UsageTableRow({ row }: { row: UsageRowView }) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap text-muted-foreground">{row.when}</TableCell>
      <TableCell className="whitespace-nowrap">{row.model}</TableCell>
      <TableCell className="whitespace-nowrap text-muted-foreground">{row.keyName}</TableCell>
      <TableCell>
        <div className="flex flex-col gap-0.5">
          <span className="flex items-center gap-2">
            <Badge variant={row.settlement.tone === "warning" ? "destructive" : "outline"}>
              {row.settlement.label}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {row.outcome} · {row.httpStatus} · {row.mode}
            </span>
          </span>
          <span className="text-xs text-muted-foreground">{row.settlement.detail}</span>
        </div>
      </TableCell>
      <TableCell className="text-right tabular-nums" title={row.tokens.note ?? undefined}>
        {row.tokens.prompt}
      </TableCell>
      <TableCell className="text-right tabular-nums" title={row.tokens.note ?? undefined}>
        {row.tokens.completion}
      </TableCell>
      <TableCell className="text-right tabular-nums">{row.amount.charged}</TableCell>
      <TableCell className="text-right tabular-nums text-muted-foreground">
        {row.amount.held ?? "—"}
      </TableCell>
    </TableRow>
  );
}
