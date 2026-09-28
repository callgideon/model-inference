// The request's trace service. The lab-api base URL is server-only configuration (LAB_TRACES_API_URL;
// unset = off, every read unavailable) and the credential is the signed-in user's own session token:
// the route verifies it and re-derives the member (lab_auth.py). No storage or service key is in the Lab.
import { authCookieOptions, labConfig } from "../../auth/config.ts";
import { httpTraces, offlineTraces, type TraceService } from "./port.ts";

export function labTraces(env: Record<string, string | undefined> = process.env): TraceService {
  const baseUrl = env.LAB_TRACES_API_URL;
  const config = labConfig(env);
  if (!baseUrl || config === null) return offlineTraces();
  return httpTraces({
    baseUrl,
    token: async () => {
      // Loaded on use: port.ts is imported under node --test, where Next's request APIs do not resolve.
      const [{ cookies }, { createServerClient }] = await Promise.all([import("next/headers"), import("@supabase/ssr")]);
      const store = await cookies();
      const client = createServerClient(config.supabaseUrl, config.anonKey, {
        cookieOptions: authCookieOptions(config),
        cookies: { getAll: () => store.getAll(), setAll: () => {} },
      });
      return (await client.auth.getSession()).data.session?.access_token ?? null;
    },
  });
}
