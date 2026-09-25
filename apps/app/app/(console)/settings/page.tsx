import Link from "next/link";
import { redirect } from "next/navigation";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { consumerSession } from "@/lib/services/server";
import { settingsModel } from "./view-model";

export const metadata = { title: "Settings · infrx" };

export default async function SettingsPage() {
  const { context } = await consumerSession();
  if (context.state === "signed_out") redirect("/login");
  const model = settingsModel(context);

  return (
    <>
      <PageHeader title="Settings" subtitle="Your account and how your data is handled." />

      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {model.account.kind === "unavailable" ? (
            <p role="status">
              {model.account.message}{" "}
              <a className="underline underline-offset-4" href="/settings">
                Try again
              </a>
            </p>
          ) : (
            <>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
                <dt className="text-muted-foreground">Email</dt>
                <dd className="min-w-0 break-all">{model.account.email}</dd>
                <dt className="text-muted-foreground">Status</dt>
                <dd>
                  {model.account.status}
                  {model.account.suspended ? (
                    <Badge variant="destructive" className="ml-2">
                      Suspended
                    </Badge>
                  ) : null}
                </dd>
              </dl>
              {model.account.suspended ? (
                <p>This account is suspended: new requests and new keys are refused. You can still read your history and revoke keys.</p>
              ) : null}
              <p>
                <Link className="underline underline-offset-4" href="/update-password">
                  Change password
                </Link>
              </p>
            </>
          )}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Privacy and data</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y">
            {model.privacy.map((row) => (
              <li key={row.title} className="space-y-1 py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-medium">{row.title}</h2>
                  <Badge variant="outline">{row.status}</Badge>
                </div>
                <p className="text-sm text-muted-foreground">
                  {row.detail}{" "}
                  {row.href === null ? null : (
                    <a className="underline underline-offset-4" href={row.href}>
                      {row.href.startsWith("mailto:") ? "Email us" : "See what we store"}
                    </a>
                  )}
                </p>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <p className="mt-6 text-sm text-muted-foreground">
        API keys are managed on{" "}
        <Link className="underline underline-offset-4" href="/api-keys">
          API Keys
        </Link>
        .
      </p>
    </>
  );
}
