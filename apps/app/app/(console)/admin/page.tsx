import { notFound } from "next/navigation";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { money, num } from "@/lib/format";
import { getSession } from "@/lib/session";
import { createAdminClient } from "@/lib/supabase/admin";
import { addCredit } from "./actions";

export const metadata = { title: "Admin · infrx" };

const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

const thirtyDaysAgo = () => new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();

export default async function AdminPage() {
  const session = await getSession();
  if (!session.isOperator) notFound();

  const admin = createAdminClient();
  const since = thirtyDaysAgo();

  // ponytail: aggregates in JS over the last 30 days of usage_events. Fine at this
  // volume; move to a SQL view when a single org passes a few hundred thousand rows.
  const [orgs, members, keys, events, ledger] = await Promise.all([
    admin.from("organizations").select("id, name, slug, created_at").order("created_at"),
    admin.from("org_members").select("org_id"),
    admin.from("api_keys").select("org_id, revoked_at"),
    admin.from("usage_events").select("org_id, cost_usd").gte("created_at", since),
    admin.from("credit_ledger").select("org_id, delta_usd"),
  ]);

  const count = <T extends { org_id: string }>(rows: T[] | null, keep: (r: T) => boolean) => {
    const map = new Map<string, number>();
    for (const r of rows ?? []) if (keep(r)) map.set(r.org_id, (map.get(r.org_id) ?? 0) + 1);
    return map;
  };
  const sum = (rows: { org_id: string }[] | null, field: string) => {
    const map = new Map<string, number>();
    for (const r of rows ?? []) {
      const v = Number((r as Record<string, unknown>)[field] ?? 0);
      map.set(r.org_id, (map.get(r.org_id) ?? 0) + v);
    }
    return map;
  };

  const memberCount = count(members.data, () => true);
  const keyCount = count(keys.data, (k) => !(k as { revoked_at: string | null }).revoked_at);
  const requestCount = count(events.data, () => true);
  const cost = sum(events.data, "cost_usd");
  const balance = sum(ledger.data, "delta_usd");
  const rows = orgs.data ?? [];

  return (
    <>
      <PageHeader title="Admin" subtitle="Every organization, and the credit ledger." />

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Organization</TableHead>
                <TableHead className="text-right">Members</TableHead>
                <TableHead className="text-right">Active keys</TableHead>
                <TableHead className="text-right">Requests 30d</TableHead>
                <TableHead className="text-right">Cost 30d</TableHead>
                <TableHead className="text-right">Balance</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((o) => (
                <TableRow key={o.id}>
                  <TableCell>
                    <div className="font-medium">{o.name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{o.slug}</div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {num(memberCount.get(o.id) ?? 0)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {num(keyCount.get(o.id) ?? 0)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {num(requestCount.get(o.id) ?? 0)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {money(cost.get(o.id) ?? 0)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {money(balance.get(o.id) ?? 0)}
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="py-10 text-center text-sm text-muted-foreground"
                  >
                    No organizations yet.
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card className="mt-4 max-w-xl">
        <CardHeader>
          <CardTitle>Add a ledger entry</CardTitle>
        </CardHeader>
        <CardContent>
          <form action={addCredit} className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="org_id">Organization</Label>
              <select id="org_id" name="org_id" required className={SELECT_CLASS}>
                {rows.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name} ({o.slug})
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="delta_usd">Amount (USD)</Label>
                <Input
                  id="delta_usd"
                  name="delta_usd"
                  type="number"
                  step="0.01"
                  required
                  placeholder="25.00"
                  className="w-full"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="kind">Kind</Label>
                <select id="kind" name="kind" defaultValue="grant" className={SELECT_CLASS}>
                  <option value="grant">grant</option>
                  <option value="purchase">purchase</option>
                  <option value="usage">usage</option>
                  <option value="adjustment">adjustment</option>
                </select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reason">Reason</Label>
              <Input id="reason" name="reason" placeholder="launch credit" className="w-full" />
            </div>
            <Button type="submit">Add entry</Button>
          </form>
        </CardContent>
      </Card>
    </>
  );
}
