import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Result } from "@/lib/contracts/types";
import { amount, credits, dateTime } from "@/lib/format";
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";
import { OperatorForms } from "./operator-forms";
import { ACCOUNT_LIMIT, operatorReads, type ReadClient } from "./operator-reads";

export const metadata = { title: "Operator · infrx" };

/** A section whose read failed says so; it never shows an empty table or a zero in its place. */
function Section<T>({ result, children }: { result: Result<T>; children: (value: T) => React.ReactNode }) {
  if (!result.ok) {
    return (
      <p role="status" className="p-4 text-sm text-destructive">
        Unavailable: {result.error.message}.
      </p>
    );
  }
  return <>{children(result.value)}</>;
}

function Empty({ cols, text }: { cols: number; text: string }) {
  return (
    <TableRow>
      <TableCell colSpan={cols} className="py-8 text-center text-sm text-muted-foreground">
        {text}
      </TableCell>
    </TableRow>
  );
}

const Mono = ({ children }: { children: React.ReactNode }) => <span className="font-mono text-xs break-all">{children}</span>;

/**
 * U3: minimal platform-operator controls. Protected operations, not the provider Lab: operator
 * authority is the platform flag, the page is 404 without it, every read uses the operator's own
 * session, and every change is one audited, idempotent operation that needs a reason.
 */
export default async function AdminPage() {
  const session = await getSession();
  if (!session.isOperator) notFound();

  const view = await operatorReads((await createClient()) as unknown as ReadClient);

  return (
    <>
      <PageHeader
        title="Operator"
        subtitle="Platform operations. Every change is audited, idempotent and needs a reason; balances are never edited directly."
      />

      <OperatorForms />

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Accounts</CardTitle>
          <CardDescription>Consumer CREDIT wallets, most recently moved first (up to {ACCOUNT_LIMIT}).</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Section result={view.accounts}>
            {(accounts) => (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Individual</TableHead>
                    <TableHead>Organization</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Balance</TableHead>
                    <TableHead className="text-right">Reserved</TableHead>
                    <TableHead className="text-right">Available</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {accounts.map((a) => (
                    <TableRow key={a.walletId}>
                      <TableCell>
                        <div className="font-medium">{a.email ?? "—"}</div>
                        <Mono>{a.userId}</Mono>
                      </TableCell>
                      <TableCell>
                        <Mono>{a.orgId}</Mono>
                      </TableCell>
                      <TableCell>
                        {a.suspended ? (
                          <Badge variant="destructive">Suspended ({a.suspensionReason ?? "no code"})</Badge>
                        ) : (
                          <Badge variant="outline">Active</Badge>
                        )}
                        {a.signupGrantedAt === null ? <div className="mt-1 text-xs text-muted-foreground">Signup grant not received</div> : null}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{credits(a.ledgerTotal)}</TableCell>
                      <TableCell className="text-right tabular-nums">{credits(a.reservedTotal)}</TableCell>
                      <TableCell className="text-right tabular-nums">{credits(a.available)}</TableCell>
                    </TableRow>
                  ))}
                  {accounts.length === 0 ? <Empty cols={6} text="No consumer wallets yet." /> : null}
                </TableBody>
              </Table>
            )}
          </Section>
        </CardContent>
      </Card>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Unknown usage awaiting reconciliation</CardTitle>
            <CardDescription>
              Requests whose token usage is unknown. The hold is never charged; after its 24 h window it is released with the audited
              operations CLI (<code>reconcile --org … --request …</code>).
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Section result={view.unknownUsage}>
              {(held) => (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Request</TableHead>
                      <TableHead>Since</TableHead>
                      <TableHead>Releasable after</TableHead>
                      <TableHead className="text-right">Held</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {held.map((h) => (
                      <TableRow key={h.requestId}>
                        <TableCell>
                          <Mono>{h.requestId}</Mono>
                          <div className="text-xs text-muted-foreground">
                            org <Mono>{h.orgId}</Mono>
                          </div>
                        </TableCell>
                        <TableCell className="whitespace-nowrap">{dateTime(h.createdAt)}</TableCell>
                        <TableCell className="whitespace-nowrap">{dateTime(h.reconcileAfter)}</TableCell>
                        <TableCell className="text-right tabular-nums">{h.hold === null ? "Released" : amount(h.hold.amount, h.hold.unit)}</TableCell>
                      </TableRow>
                    ))}
                    {held.length === 0 ? <Empty cols={4} text="Nothing is waiting for reconciliation." /> : null}
                  </TableBody>
                </Table>
              )}
            </Section>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Wallet reconciliation</CardTitle>
            <CardDescription>Wallets whose summary differs from their ledger and active holds. Any row here is an incident.</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Section result={view.drift}>
              {(drift) => (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Wallet</TableHead>
                      <TableHead className="text-right">Ledger drift</TableHead>
                      <TableHead className="text-right">Reserved drift</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {drift.map((d) => (
                      <TableRow key={d.walletId}>
                        <TableCell>
                          <Mono>{d.walletId}</Mono>
                          <div className="text-xs text-muted-foreground">{d.kind}</div>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{credits(d.ledgerDrift)}</TableCell>
                        <TableCell className="text-right tabular-nums">{credits(d.reservedDrift)}</TableCell>
                      </TableRow>
                    ))}
                    {drift.length === 0 ? <Empty cols={3} text="Every wallet reconciles with its ledger and holds." /> : null}
                  </TableBody>
                </Table>
              )}
            </Section>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Rates</CardTitle>
          <CardDescription>
            Rates are published only as approved, immutable rate cards with the audited operations CLI (<code>publish-card</code>). A
            new card applies to newly admitted requests only. The published catalog is on{" "}
            <Link href="/models" className="underline underline-offset-4">
              Models
            </Link>
            .
          </CardDescription>
        </CardHeader>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Audit trail</CardTitle>
          <CardDescription>The newest operator writes. Append-only.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Section result={view.audit}>
            {(entries) => (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Actor</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell className="whitespace-nowrap">{dateTime(e.at)}</TableCell>
                      <TableCell>
                        <Mono>{e.action}</Mono>
                      </TableCell>
                      <TableCell>
                        <Mono>{e.actor}</Mono>
                      </TableCell>
                      <TableCell>{e.targetOrgId === null ? "—" : <Mono>{e.targetOrgId}</Mono>}</TableCell>
                      <TableCell className="max-w-xs break-words">{e.reason}</TableCell>
                    </TableRow>
                  ))}
                  {entries.length === 0 ? <Empty cols={5} text="No operator writes yet." /> : null}
                </TableBody>
              </Table>
            )}
          </Section>
        </CardContent>
      </Card>
    </>
  );
}
