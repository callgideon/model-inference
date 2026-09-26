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
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";

export const metadata = { title: "Teams · infrx" };

export default async function TeamsPage() {
  const session = await getSession();
  providerRoute(session, notFound);
  const supabase = await createClient();
  const { data } = await supabase
    .from("org_members")
    .select("role, profiles(email)")
    .eq("org_id", session.orgId);

  const members = (data ?? []).map((m) => ({
    role: m.role as string,
    email: (m.profiles as unknown as { email: string } | null)?.email ?? "—",
  }));

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
            <div className="font-heading text-base font-medium">{session.orgName}</div>
          </div>
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
        </CardContent>
      </Card>
    </>
  );
}
