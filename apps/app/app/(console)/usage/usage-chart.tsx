"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { compact, money, num } from "@/lib/format";
import type { UsageDay } from "@/lib/types";

/** Daily tokens. Pure Tailwind bars — a chart library is not worth 30 rows. */
export function UsageChart({ days }: { days: UsageDay[] }) {
  const rows = days.map((d) => ({
    ...d,
    total: Number(d.prompt_tokens) + Number(d.completion_tokens),
  }));
  const max = Math.max(1, ...rows.map((r) => r.total));

  return (
    <Tabs defaultValue="graph">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium">Tokens per day</h3>
        <TabsList>
          <TabsTrigger value="graph">Graph</TabsTrigger>
          <TabsTrigger value="table">Table</TabsTrigger>
        </TabsList>
      </div>

      <TabsContent value="graph">
        {rows.length === 0 ? (
          <Empty />
        ) : (
          <div className="flex h-40 items-end gap-1 overflow-x-auto border-b pb-1">
            {rows.map((r) => (
              <div key={r.day} className="group flex min-w-6 flex-1 flex-col items-center gap-1">
                <div className="w-full flex-1 flex items-end">
                  <div
                    className="w-full rounded-t-sm bg-primary/70 transition-colors group-hover:bg-primary"
                    style={{ height: `${Math.max(2, (r.total / max) * 100)}%` }}
                    title={`${r.day}: ${num(r.total)} tokens`}
                  />
                </div>
                <span className="text-[10px] text-muted-foreground">{r.day.slice(5)}</span>
              </div>
            ))}
          </div>
        )}
      </TabsContent>

      <TabsContent value="table">
        {rows.length === 0 ? (
          <Empty />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Day</TableHead>
                <TableHead className="text-right">Requests</TableHead>
                <TableHead className="text-right">Input</TableHead>
                <TableHead className="text-right">Output</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.day}>
                  <TableCell>{r.day}</TableCell>
                  <TableCell className="text-right tabular-nums">{num(r.requests)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {compact(r.prompt_tokens)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {compact(r.completion_tokens)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{money(r.cost_usd)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </TabsContent>
    </Tabs>
  );
}

function Empty() {
  return (
    <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
      No requests in this range yet.
    </div>
  );
}
