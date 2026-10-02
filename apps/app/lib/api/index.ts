// AP-00 00c: the App's transport port over the generated consumer client (`packages/api-client`).
// Nothing uses it yet: AP-09 moves the App's reads and actions onto it, one surface at a time.
// Server-side only - the session it forwards is the signed-in user's; it holds no key and reads no
// environment. Imports are relative until the App links `@infrx/api-client` (an AP-00 wiring request).
import type { paths } from "../../../../packages/api-client/src/consumer.ts";
import { createClient, type Client, type ClientOptions } from "../../../../packages/api-client/src/transport.ts";

export type { ApiError, Result, Session } from "../../../../packages/api-client/src/transport.ts";
export type ConsumerApi = Client<paths>;

export function consumerApi(options: ClientOptions): ConsumerApi {
  return createClient<paths>(options);
}
