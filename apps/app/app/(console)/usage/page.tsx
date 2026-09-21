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
import { balanceCardState, historyProbeQuery } from "../billing/view-model";
import { consoleContext } from "./fake-console-context";
import { EmptyPanel, ErrorPanel, Pager } from "./states";
import { UsageChart } from "./usage-chart";
import { UsageControls } from "./usage-controls";
import {
  parseUsageFilters,
  usagePageModel,
  usagePageQuery,
  usageScopeQuery,
  type UsageRowView,
} from "./view-model";

export const metadata = { title: "Usage · infrx" };

export default async function UsagePage({ searchParams }: PageProps<"/usage">) {
  const params = await searchParams;
  const { services, session, now } = consoleContext();
  const filters = parseUsageFilters(params);
  const scope = usageScopeQuery(filters, now);

  const [usage, summary, daily, keys, balance, history] = await Promise.all([
    services.usage(session, usagePageQuery(filters, now)),
    services.usageSummary(session, scope),
    services.usageDaily(session, scope),
    services.keys.list(session),
    services.balances(session),
    services.ledger(session, historyProbeQuery()),
  ]);

  const model = usagePageModel({ filters, usage, summary, daily, keys });
  const card = balanceCardState(balance, history);

  return (
    <>
      <PageHeader
        title="Usage"
        subtitle="Metadata only — no prompts or video are stored. Amounts are drawn from promotional pilot credit."
        action={
          <UsageControls filters={model.filters} keys={model.keyOptions} models={model.models} />
        }
      />

      {model.keys.kind === "error" ? (
        <p role="status" className="mb-3 text-sm text-destructive">
          The API key list could not be loaded, so the key filter is incomplete. {model.keys.message}
        </p>
      ) : null}
      {model.keyNotice === null ? null : (
        <p role="status" className="mb-3 text-sm text-destructive">
          {model.keyNotice}
        </p>
      )}

      {model.summary.kind === "ready" ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {model.summary.value.map((tile) => (
            <StatTile key={tile.label} label={tile.label} value={tile.value} hint={tile.hint} />
          ))}
        </div>
      ) : null}
      {model.summary.kind === "error" ? (
        <ErrorPanel
          title="This range could not be totalled"
          state={model.summary}
          href={model.here}
          firstPageHref={model.firstHref}
        />
      ) : null}

      <div className="mt-6 grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Card>
          <CardContent>
            {model.daily.kind === "error" ? (
              <ErrorPanel
                title="The daily breakdown could not be loaded"
                state={model.daily}
                href={model.here}
                firstPageHref={model.firstHref}
              />
            ) : (
              <UsageChart days={model.daily.kind === "ready" ? model.daily.value : []} />
            )}
          </CardContent>
        </Card>
        {card.kind === "ready" ? <PromotionalBalanceCard model={card.value} /> : null}
        {card.kind === "error" ? (
          <ErrorPanel
            title="Your balance could not be loaded"
            state={card}
            href={model.here}
            firstPageHref={model.firstHref}
          />
        ) : null}
      </div>

      <h2 className="mt-8 mb-3 font-heading text-base font-medium">Requests</h2>

      {model.rows.kind === "error" ? (
        <ErrorPanel
          title="These requests could not be loaded"
          state={model.rows}
          href={model.here}
          firstPageHref={model.firstHref}
        />
      ) : null}

      {model.rows.kind === "empty" ? (
        <EmptyPanel>
          No requests in this range. Widen the time range, or clear the key and model filters.
        </EmptyPanel>
      ) : null}

      {model.rows.kind === "ready" ? (
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
                  {model.rows.value.rows.map((row) => (
                    <UsageTableRow key={row.requestId} row={row} />
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
