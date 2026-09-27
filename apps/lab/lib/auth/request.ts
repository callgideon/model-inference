// L1: one request's provider access, from its cookies and a Supabase session client. Everything but
// the Next/Supabase constructors lives here, so the guard's decisions run under node --test.
import { resolveAccess, type Access } from "./access.ts";
import { WORKSPACE_COOKIE, authCookieOptions, labConfig, type LabConfig } from "./config.ts";
import { readMemberships, type RpcClient } from "./memberships.ts";

type Cookie = { name: string; value: string };
export type CookieStore = {
  getAll(): Cookie[];
  get(name: string): Cookie | undefined;
  set(name: string, value: string, options?: object): unknown;
};
export type SessionClient = RpcClient & {
  auth: { getUser(): PromiseLike<{ data: { user: { id: string } | null } }> };
};
export type ClientOptions = {
  cookieOptions: ReturnType<typeof authCookieOptions>;
  cookies: { getAll(): Cookie[]; setAll(list: (Cookie & { options?: object })[]): void };
};

/** The Lab session client's options over a request cookie store (guard, sign-in and sign-out). */
export function clientOptions(config: LabConfig, store: CookieStore): ClientOptions {
  return {
    cookieOptions: authCookieOptions(config),
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list) => {
        try {
          for (const { name, value, options } of list) store.set(name, value, options);
        } catch {
          // A server component cannot set cookies; the next action or route refreshes them.
        }
      },
    },
  };
}

export async function accessFromRequest(
  env: Record<string, string | undefined>,
  store: CookieStore,
  makeClient: (url: string, key: string, options: ClientOptions) => SessionClient,
): Promise<Access> {
  const config = labConfig(env);
  if (config === null) return { kind: "unavailable" };
  const client = makeClient(config.supabaseUrl, config.anonKey, clientOptions(config, store));
  return resolveAccess({
    userId: async () => (await client.auth.getUser()).data.user?.id ?? null,
    memberships: () => readMemberships(client),
    selected: store.get(WORKSPACE_COOKIE)?.value,
  });
}
