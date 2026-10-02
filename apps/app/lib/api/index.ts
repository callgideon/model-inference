// AP-00 00c: the App's transport port over the generated consumer client (`packages/api-client`).
// Nothing uses it yet: AP-09 moves the App's reads and actions onto it, one surface at a time.
// Server-side only - the session it forwards is the signed-in user's; it holds no key and reads no
// environment. Imports go through the linked `@infrx/api-client` package (AP-00 wiring WR-1).
import type { paths } from "@infrx/api-client/consumer";
import { createClient, type Client, type ClientOptions } from "@infrx/api-client/transport";

export type { ApiError, Result, Session } from "@infrx/api-client/transport";
export type ConsumerApi = Client<paths>;

export function consumerApi(options: ClientOptions): ConsumerApi {
  return createClient<paths>(options);
}
