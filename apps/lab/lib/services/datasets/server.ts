// N4: the request's datasets port. The backend URL is server-only configuration and the credential is
// the signed-in user's own session token (the backend verifies it and re-derives the provider: WR-N-2);
// no storage or service key exists in the Lab. Unconfigured or tokenless, every call is unavailable.
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { authCookieOptions, labConfig } from "../../auth/config.ts";
import { httpDatasets, offlineDatasets, type DatasetsPort } from "./port.ts";

export async function datasetsPort(): Promise<DatasetsPort> {
  const baseUrl = process.env.LAB_DATASETS_API_URL;
  const config = labConfig(process.env);
  if (!baseUrl || config === null) return offlineDatasets();
  const store = await cookies();
  const client = createServerClient(config.supabaseUrl, config.anonKey, {
    cookieOptions: authCookieOptions(config),
    cookies: { getAll: () => store.getAll(), setAll: () => {} },
  });
  const token = (await client.auth.getSession()).data.session?.access_token;
  return token ? httpDatasets({ baseUrl, token }) : offlineDatasets("the session has no token");
}
