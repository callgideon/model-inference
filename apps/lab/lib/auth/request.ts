// L1: one request's provider access, from its cookies and the API. Everything but Next's cookie store
// lives here, so the guard's decisions run under node --test (`send` is the fetch the tests fake).
import { labApi } from "../api/index.ts";
import { resolveAccess, type Access } from "./access.ts";
import { AUTH_COOKIE, WORKSPACE_COOKIE, labConfig } from "./config.ts";
import { readMemberships } from "./memberships.ts";

export type CookieStore = { get(name: string): { value: string } | undefined };

export async function accessFromRequest(env: Record<string, string | undefined>, store: CookieStore, send?: typeof fetch): Promise<Access> {
  const config = labConfig(env);
  if (config === null) return { kind: "unavailable" };
  const token = store.get(AUTH_COOKIE)?.value || null;
  const api = labApi({ baseUrl: config.apiUrl, session: () => ({ token }), fetch: send });
  return resolveAccess({ signedIn: token !== null, memberships: () => readMemberships(api), selected: store.get(WORKSPACE_COOKIE)?.value });
}
