/**
 * C-07: the data-use section of Settings - per-key trace capture and the grants the account made to
 * providers - rendered from `readDataUse`'s honest state. A failed read shows no choice and no empty
 * list; only `ready` renders controls. The actions are passed in (the page hands it the settings
 * server actions), so this renders from fixtures with no server behind it.
 */
import type { DataGrant, DataUseRead } from "@/lib/services/data-use";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CaptureForm, WithdrawForm, type SaveAction, type WithdrawAction } from "./data-use-controls";

const utc = (iso: string) => `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
const words = (values: string[]) => values.map((v) => v.replaceAll("_", " ")).join(", ");

function grantState(grant: DataGrant): string {
  if (grant.state === "revoked" && grant.revokedAt !== null) return `Withdrawn ${utc(grant.revokedAt)}`;
  if (grant.state === "expired" && grant.expiresAt !== null) return `Expired ${utc(grant.expiresAt)}`;
  if (grant.state === "active") return grant.expiresAt === null ? "Active" : `Active until ${utc(grant.expiresAt)}`;
  return grant.state === "revoked" ? "Withdrawn" : "Expired";
}

function Body({ read, suspended, save, withdraw }: Props) {
  switch (read.kind) {
    case "unavailable":
      return (
        <p role="status">
          Data-use settings are unavailable right now.{" "}
          <a className="underline underline-offset-4" href="/settings">
            Try again
          </a>
        </p>
      );
    case "forbidden":
      return <p role="status">Only the account&apos;s owner can see and change its data use.</p>;
    case "signed_out":
      return (
        <p role="status">
          Your session has ended.{" "}
          <a className="underline underline-offset-4" href="/login">
            Sign in again
          </a>{" "}
          to change data use.
        </p>
      );
  }
  const { consentVersion, retentionDays, keys, grants } = read.value;
  return (
    <>
      <p className="text-muted-foreground">
        Capture is chosen per key and applies to requests sent after you save. Metadata only records a request&apos;s
        details but not its content; Full content also records the request and its output. Captured content is kept for{" "}
        {retentionDays} days. Turning capture off does not change what we store to run a request (see Serving retention).
      </p>
      {suspended ? <p role="status">This account is suspended: capture cannot be changed. You can still withdraw a grant.</p> : null}
      {keys.length === 0 ? (
        <p>You have no active keys. Capture is chosen per key: create one on API Keys first.</p>
      ) : (
        <ul className="divide-y">
          {keys.map((key) => (
            <li key={key.id} className="py-2 first:pt-0">
              <CaptureForm
                keyId={key.id}
                name={key.name}
                mode={key.mode}
                effectiveMode={key.effectiveMode}
                consentVersion={consentVersion}
                retentionDays={retentionDays}
                suspended={suspended}
                save={save}
              />
            </li>
          ))}
        </ul>
      )}
      <h3 className="font-medium">Grants to providers</h3>
      {grants.length === 0 ? (
        <p>You have not granted any provider use of your data.</p>
      ) : (
        <ul className="divide-y">
          {grants.map((grant) => (
            <li key={grant.id} className="space-y-1 py-2 first:pt-0">
              <p className="break-all">
                Provider {grant.providerOrgId}: {words(grant.purposes)} of {words(grant.categories)} for{" "}
                {grant.modelIds.join(", ")}, kept {grant.retentionDays} days.
              </p>
              <p className="text-muted-foreground">{grantState(grant)}</p>
              {grant.state === "active" ? <WithdrawForm grantId={grant.id} withdraw={withdraw} /> : null}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

type Props = { read: DataUseRead; suspended: boolean; save: SaveAction; withdraw: WithdrawAction };

export function DataUseSection(props: Props) {
  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>Trace capture and data grants</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <Body {...props} />
      </CardContent>
    </Card>
  );
}
