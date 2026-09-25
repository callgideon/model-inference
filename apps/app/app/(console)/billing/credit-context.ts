import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import orgsFixture from "@/lib/contracts/fixtures/orgs.json";
import { previewAllowed } from "../usage/fake-console-context";
import { fixtureCreditReads } from "./credit-fixture";
import { postgrestCreditReads, type CreditClient, type CreditReads } from "./credit-reads";

/**
 * The Usage and Credits pages' data source (U1R). Production is the signed-in user's own Supabase
 * session — the cookie client of `lib/supabase/server.ts`: anon key plus the user's JWT, RLS on, no
 * service credential — and the user id the auth server verified. The fixture is reachable only
 * through the console preview gate, which a production build folds to "never".
 */
export async function consumerCreditReads(): Promise<{ reads: CreditReads; preview: boolean; now: Date }> {
  if (previewAllowed()) {
    return { reads: fixtureCreditReads(), preview: true, now: new Date(orgsFixture.clock) };
  }
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  if (data.user === null) redirect("/login");
  // The server client implements the slice of supabase-js the adapter declares (`CreditClient`).
  return {
    reads: postgrestCreditReads(supabase as unknown as CreditClient, data.user.id),
    preview: false,
    now: new Date(),
  };
}
