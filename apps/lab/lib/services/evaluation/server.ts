// WR-B4-1 swap: the request's evaluation adapter. The lab-api base URL is server-only configuration
// (LAB_EVALS_API_URL; unset = null, the port stays unavailable) and the credential is the signed-in
// user's own session token (lab_auth re-derives the member); the Lab holds no storage or service key.
// `sessionToken` is shared with the pipelines and releases adapters.
import { authCookieOptions, labConfig, type LabConfig } from "../../auth/config.ts";
import type { EvaluationPort } from "./port.ts";
import { httpEvaluation } from "./http.ts";

/** The signed-in user's access token from the Lab session cookie; null without a session. */
export function sessionToken(config: LabConfig): () => Promise<string | null> {
  return async () => {
    // Loaded on use: the ports are imported under node --test, where Next's request APIs do not resolve.
    const [{ cookies }, { createServerClient }] = await Promise.all([import("next/headers"), import("@supabase/ssr")]);
    const store = await cookies();
    const client = createServerClient(config.supabaseUrl, config.anonKey, {
      cookieOptions: authCookieOptions(config),
      cookies: { getAll: () => store.getAll(), setAll: () => {} },
    });
    return (await client.auth.getSession()).data.session?.access_token ?? null;
  };
}

export function labEvaluation(env: Record<string, string | undefined>): EvaluationPort | null {
  const baseUrl = env.LAB_EVALS_API_URL;
  const config = labConfig(env);
  if (!baseUrl || config === null) return null;
  return httpEvaluation({ baseUrl, token: sessionToken(config) });
}
