// WR-R4-1 swap: the request's releases adapter. The lab-api base URL is server-only configuration
// (LAB_RELEASES_API_URL; unset = null, the port stays unavailable) and the credential is the signed-in
// user's own session token (evaluation/server.ts); the Lab holds no storage or service key.
import { labConfig } from "../../auth/config.ts";
import { sessionToken } from "../evaluation/server.ts";
import { httpReleases } from "./http.ts";
import type { ReleasesPort } from "./port.ts";

export function labReleases(env: Record<string, string | undefined>): ReleasesPort | null {
  const baseUrl = env.LAB_RELEASES_API_URL;
  const config = labConfig(env);
  if (!baseUrl || config === null) return null;
  return httpReleases({ baseUrl, token: sessionToken(config) });
}
