// AP-00 00c: the Lab's transport port over the generated Lab client (`packages/api-client`).
// AP-09 09c: the session (lib/auth/session.ts), memberships (lib/auth/memberships.ts), judge and
// review (lib/services/{judge,review}) call the API through it; the older families keep
// lib/services/http.ts. Server-side only - the session it forwards is the signed-in user's access
// token; it holds no key and reads no environment. Imports go through the linked
// `@infrx/api-client` package (AP-00 wiring WR-1).
import type { paths } from "@infrx/api-client/lab";
import { createClient, type Client, type ClientOptions } from "@infrx/api-client/transport";

export type { ApiError, Result, Session } from "@infrx/api-client/transport";

type Json<T> = { content: { "application/json": T } };
type Get<Q, T> = { parameters: { query?: Q }; responses: { 200: Json<T> } };
type Post<B, T> = { requestBody: Json<B>; responses: { 200: Json<T> } };
export type Tokens = { access_token: string; refresh_token: string; token_type: string; expires_in: number; expires_at?: number | null; user_id: string };
export type Availability = { state: "configured" | "disabled" | "unavailable" | "unknown"; reason?: string | null; verified_at?: string | null };
export type Workspace = { provider_org_id: string; provider_name: string; role: string; capabilities: string[] };

/**
 * The Lab shell's operations the Lab unit does not export yet: AP-01's `/lab/v1/workspaces` and
 * `/lab/v1/capabilities` and the auth facade's `/auth/v1/*`, today mounted on the gateway only
 * (`apps/infrx-api/openapi/consumer.json`; tests/boundary/api.test.ts pins these names to it).
 * ponytail: hand-written until the Lab unit mounts them (api-identity-2's lab_workspaces mount +
 * WR-AP09L-2) and `pnpm generate` puts them in `paths`; then this type goes.
 */
export type ShellPaths = {
  "/lab/v1/workspaces": { get: Get<never, { data: Workspace[]; next_cursor?: string | null }> };
  "/lab/v1/capabilities": {
    get: Get<{ provider_org_id: string }, { provider_org_id: string; role: string; capabilities: string[]; features: Record<string, Availability> }>;
  };
  "/auth/v1/sign-in": { post: Post<{ email: string; password: string }, Tokens> };
  "/auth/v1/refresh": { post: Post<{ refresh_token: string }, Tokens> };
  "/auth/v1/sign-out": { post: { responses: { 204: { content?: never } } } };
  "/auth/v1/callback": { get: Get<{ code?: string; token_hash?: string; type?: string }, { session: Tokens; redirect: string }> };
};

export type LabApi = Client<paths & ShellPaths>;

export function labApi(options: ClientOptions): LabApi {
  return createClient<paths & ShellPaths>(options);
}
