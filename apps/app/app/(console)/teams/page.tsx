import { notFound } from "next/navigation";
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
import { providerRoute } from "@/lib/services/console";
import { apiSource } from "@/lib/api/server";
import { getSession } from "@/lib/session";

export const metadata = { title: "Teams · infrx" };

export default async function TeamsPage() {
  const session = await getSession();
  providerRoute(session, notFound);
  // The personal organization's members, as infrx-api reads them for this session (`/console/v1/account/members`).
  const answer = await (await apiSource()).api.call("get", "/console/v1/account/members", { query: { limit: 100 } });
  const members = answer.ok ? answer.data.data.map((m) => ({ role: m.role, email: m.email ?? "—" })) : null;

  return (
    <>
      <PageHeader
        title="Teams"
        subtitle="One organization per account at launch. Invites come with the next slice."
      />

      <Card>
        <CardContent className="space-y-4">
          <div>
            <div className="text-xs text-muted-foreground">Organization</div>
            <div className="font-heading text-base font-medium">Personal</div>
          </div>
          {members === null ? (
            <p role="status" className="text-sm text-muted-foreground">
              Members could not be loaded right now. Reload to try again.
            </p>
          ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Member</TableHead>
                <TableHead>Role</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((m) => (
                <TableRow key={m.email}>
                  <TableCell>{m.email}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{m.role}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
