// LAB-05: the Lab session's read-only client, built in one place. It replaces the copies in
// evaluation/server.ts (sessionToken, shared by control/pipelines/rollouts), traces/server.ts and
// datasets/server.ts. Read-only: setAll is a no-op, because the guard's client (guard.ts) already
// refreshed the session cookies for this request; the cookie-refreshing clients (guard.ts, routes.ts,
// sign-in.ts) and judge's writing sessionRpc stay their own. Next's request APIs load on use, so the
// ports that import this still load under node --test.
import { authCookieOptions, type LabConfig } from "./config.ts";

export async function readOnlyClient(config: LabConfig) {
  const [{ cookies }, { createServerClient }] = await Promise.all([import("next/headers"), import("@supabase/ssr")]);
  const store = await cookies();
  return createServerClient(config.supabaseUrl, config.anonKey, {
    cookieOptions: authCookieOptions(config),
    cookies: { getAll: () => store.getAll(), setAll: () => {} },
  });
}

/** The signed-in user's access token from the Lab session cookie; null without a session. */
export function sessionToken(config: LabConfig): () => Promise<string | null> {
  return async () => (await (await readOnlyClient(config)).auth.getSession()).data.session?.access_token ?? null;
}
