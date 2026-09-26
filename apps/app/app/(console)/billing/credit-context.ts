import { redirect } from "next/navigation";
import { requestClient } from "@/lib/session";
import { creditSource, type CreditSource } from "./credit-fixture";
import { postgrestCreditReads, type CreditClient } from "./credit-reads";

/**
 * The Usage and Credits pages' data source (U1R). Production is the signed-in user's own Supabase
 * session — the request's one cookie client (`requestClient`: anon key plus the user's JWT, RLS on,
 * no service credential, one `getUser()` per request, U1R WR-6) — and the user id the auth server
 * verified. Whether the fixture is served is decided only by `creditSource` (tested,
 * U1R-G01..G03); this glue has no gate of its own.
 */
export function consumerCreditReads(): Promise<CreditSource> {
  return creditSource(async () => {
    const supabase = await requestClient();
    const { data } = await supabase.auth.getUser();
    if (data.user === null) redirect("/login");
    // The server client implements the slice of supabase-js the adapter declares (`CreditClient`).
    return {
      reads: postgrestCreditReads(supabase as unknown as CreditClient, data.user.id),
      preview: false,
      now: new Date(),
    };
  });
}
