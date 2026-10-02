import { apiSource } from "@/lib/request-api";
import { apiCreditReads, type CreditReads } from "./credit-reads";

export type CreditSource = { reads: CreditReads; preview: boolean; now: Date };

/**
 * The Usage and Credits pages' data source (U1R): infrx-api as the signed-in user
 * (`lib/request-api.ts`), or the client fake in the development preview - chosen there, by the one
 * preview gate (`usage/fake-console-context.ts`), never here.
 */
export async function consumerCreditReads(): Promise<CreditSource> {
  const { api, preview, now } = await apiSource();
  return { reads: apiCreditReads(api), preview, now };
}
