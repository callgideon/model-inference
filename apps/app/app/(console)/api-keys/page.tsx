import { redirect } from "next/navigation";
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
import { consumerSession } from "@/lib/services/server";
import { CreateKeyDialog } from "./create-key-dialog";
import { RevokeButton } from "./revoke-button";
import { LOST_KEY_COPY, REVOCATION_COPY, keysPageModel } from "./view-model";

export const metadata = { title: "API Keys · infrx" };

export default async function ApiKeysPage() {
  // C0: the signed-in individual's own consumer account and read port; every state below is the model's.
  const { context, reads } = await consumerSession();
  if (context.state === "signed_out") redirect("/login");
  const model = keysPageModel(context, reads === null ? null : await reads.keys());

  return (
    <>
      <PageHeader
        title="API Keys"
        subtitle="Send a key as Authorization: Bearer <key>. Each key is shown once, when you create it."
        action={model.create.allowed ? <CreateKeyDialog /> : null}
      />

      {model.create.allowed ? null : (
        <p role="status" className="mb-4 rounded-md border p-3 text-sm">
          {model.create.reason}
        </p>
      )}

      <Card>
        <CardContent className="p-0">
          {model.list.kind === "unavailable" ? (
            <div role="status" className="space-y-2 p-6 text-sm">
              <p>{model.list.message}</p>
              {model.list.retry ? (
                <a className="underline underline-offset-4" href="/api-keys">
                  Try again
                </a>
              ) : null}
            </div>
          ) : model.list.kind === "empty" ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              No keys yet.{model.create.allowed ? " Create one to start calling the API." : ""}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead className="w-10">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {model.list.rows.map((k) => (
                  <TableRow key={k.id}>
                    <TableCell className="font-medium">{k.name}</TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                      {k.prefix}
                      {k.revoked === null ? null : (
                        <Badge variant="destructive" className="ml-2" title={`Revoked ${k.revoked}`}>
                          Revoked
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground">{k.created}</TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground">{k.lastUsed}</TableCell>
                    <TableCell>{k.revocable ? <RevokeButton id={k.id} name={k.name} /> : null}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Keeping keys safe</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>Keys are shown once and stored as a hash. Only the prefix above is kept in clear, so you can tell keys apart.</p>
          <p>{LOST_KEY_COPY}</p>
          <p>{REVOCATION_COPY}</p>
        </CardContent>
      </Card>
    </>
  );
}
