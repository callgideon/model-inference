/**
 * The server-only edge of the console services: the request's infrx-api client (`lib/request-api.ts`)
 * handed to C0's testable logic. No credential, cursor secret or database client lives here.
 */

import { cache } from "react";
import { apiSource, sessionEmail } from "../request-api";
import { consumerSessionFrom, type ConsumerSession } from "./console";

/** C0: the signed-in individual's consumer account and read port, once per request. */
export const consumerSession = cache(async (): Promise<ConsumerSession> => {
  const [{ api }, email] = await Promise.all([apiSource(), sessionEmail()]);
  return consumerSessionFrom(api, email);
});
