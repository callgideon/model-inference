// AP-00 00c: the Lab's transport port over the generated Lab client (`packages/api-client`).
// Nothing uses it yet: AP-09 moves the Lab's families onto it (lib/services/http.ts today).
// Server-side only - the session it forwards is the signed-in user's access token; it holds no key
// and reads no environment. Imports are relative until the Lab links `@infrx/api-client` (an AP-00
// wiring request).
import type { paths } from "../../../../packages/api-client/src/lab.ts";
import { createClient, type Client, type ClientOptions } from "../../../../packages/api-client/src/transport.ts";

export type { ApiError, Result, Session } from "../../../../packages/api-client/src/transport.ts";
export type LabApi = Client<paths>;

export function labApi(options: ClientOptions): LabApi {
  return createClient<paths>(options);
}
