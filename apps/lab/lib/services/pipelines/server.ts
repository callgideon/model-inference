// WR-P4-1 swap: the request's pipelines adapter. The lab-api base URL is server-only configuration
// (LAB_API_URL, else LAB_PIPELINES_API_URL; unset = null, the port stays unavailable) and the credential
// is the signed-in user's own session token (lib/auth/session.ts); the Lab holds no storage or service
// key.
import { labApiUrl, labConfig } from "../../auth/config.ts";
import { sessionToken } from "../../auth/session.ts";
import { httpPipelines } from "./http.ts";
import type { PipelinesPort } from "./port.ts";

export function labPipelines(env: Record<string, string | undefined>): PipelinesPort | null {
  const baseUrl = labApiUrl(env, "pipelines");
  const config = labConfig(env);
  if (!baseUrl || config === null) return null;
  return httpPipelines({ baseUrl, token: sessionToken(config) });
}
