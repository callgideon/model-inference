import { accessToken, apiSource } from "@/lib/api/server";
import { apiRequestReads, type RequestSource } from "./request-reads";

/**
 * The request detail's data source (U4): infrx-api as the signed-in user (`lib/api/server.ts`;
 * the client fake in the development preview). `null` when there is no session at all: the page
 * sends the reader to sign in, the result route answers 401.
 */
export async function consumerRequestReads(): Promise<RequestSource | null> {
  const { api, preview } = await apiSource();
  if (!preview && (await accessToken()) === null) return null;
  return { reads: apiRequestReads(api), preview };
}
