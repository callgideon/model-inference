// The request's trace service. The lab-api base URL is server-only configuration (LAB_API_URL, else
// LAB_TRACES_API_URL; unset = off, every read unavailable) and the credential is the signed-in user's own
// session token (lib/auth/session.ts): the route verifies it and re-derives the member (lab_auth.py). No
// storage or service key is in the Lab.
import { labApiUrl, labConfig } from "../../auth/config.ts";
import { sessionToken } from "../../auth/session.ts";
import { httpTraces, offlineTraces, type TraceService } from "./port.ts";

export function labTraces(env: Record<string, string | undefined> = process.env): TraceService {
  const baseUrl = labApiUrl(env, "traces");
  const config = labConfig(env);
  if (!baseUrl || config === null) return offlineTraces();
  return httpTraces({ baseUrl, token: sessionToken(config) });
}
