import { createClient } from "@/lib/supabase/server";
import type { CreditClient } from "../../billing/credit-reads";
import { postgrestRequestReads, requestSource, type RequestSource } from "./request-reads";

/**
 * The request detail's data source (U4): the signed-in user's own Supabase session — the cookie
 * client of `lib/supabase/server.ts` (anon key plus the user's JWT, RLS on, no service credential)
 * and the user id the auth server verified. `null` when nobody is signed in: the page sends the
 * reader to sign in, the result route answers 401. The fixture is chosen only by `requestSource`
 * (tested, U4-G01); this glue has no gate of its own.
 */
export function consumerRequestReads(): Promise<RequestSource | null> {
  return requestSource(async () => {
    const supabase = await createClient();
    const { data } = await supabase.auth.getUser();
    if (data.user === null) return null;
    return { reads: postgrestRequestReads(supabase as unknown as CreditClient, data.user.id), preview: false };
  });
}
