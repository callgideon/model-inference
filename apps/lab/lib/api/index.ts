// AP-00 00c: the Lab's transport port over the generated Lab client (`packages/api-client`).
// Nothing uses it yet: AP-09 moves the Lab's families onto it (lib/services/http.ts today).
// Server-side only - the session it forwards is the signed-in user's access token; it holds no key
// and reads no environment. Imports go through the linked `@infrx/api-client` package (AP-00 wiring
// WR-1).
import type { paths } from "@infrx/api-client/lab";
import { createClient, type Client, type ClientOptions } from "@infrx/api-client/transport";

export type { ApiError, Result, Session } from "@infrx/api-client/transport";
export type LabApi = Client<paths>;

export function labApi(options: ClientOptions): LabApi {
  return createClient<paths>(options);
}
