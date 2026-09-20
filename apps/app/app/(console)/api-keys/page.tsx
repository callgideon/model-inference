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
import { date, dateTime } from "@/lib/format";
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";
import type { ApiKey } from "@/lib/types";
import { CreateKeyDialog } from "./create-key-dialog";
import { RevokeButton } from "./revoke-button";

export const metadata = { title: "API Keys · infrx" };

export default async function ApiKeysPage() {
  const session = await getSession();
  const supabase = await createClient();
  const { data } = await supabase
    .from("api_keys")
    .select("id, name, prefix, created_at, last_used_at, revoked_at")
    .eq("org_id", session.orgId)
    .order("created_at", { ascending: false });

  const keys = (data ?? []) as ApiKey[];
  const isOwner = session.role === "owner";

  return (
    <>
      <PageHeader
        title="API Keys"
        subtitle="Keys are shown once at creation and stored hashed. Send them as Authorization: Bearer."
        action={isOwner ? <CreateKeyDialog /> : null}
      />

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Key</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {keys.map((k) => (
                <TableRow key={k.id}>
                  <TableCell className="font-medium">{k.name}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {k.prefix}…
                    {k.revoked_at ? (
                      <Badge variant="destructive" className="ml-2">
                        Revoked
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{date(k.created_at)}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {k.last_used_at ? dateTime(k.last_used_at) : "Never"}
                  </TableCell>
                  <TableCell>
                    {isOwner && !k.revoked_at ? <RevokeButton id={k.id} name={k.name} /> : null}
                  </TableCell>
                </TableRow>
              ))}
              {keys.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={5}
                    className="py-10 text-center text-sm text-muted-foreground"
                  >
                    No keys yet.{" "}
                    {isOwner
                      ? "Create one to start calling the API."
                      : "Ask an owner to create one."}
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  );
}
