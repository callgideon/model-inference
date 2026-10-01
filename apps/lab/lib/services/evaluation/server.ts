// WR-B4-1 swap: the request's evaluation adapter. The lab-api base URL is server-only configuration
// (LAB_API_URL, else LAB_EVALS_API_URL; unset = null, the port stays unavailable) and the credential is
// the signed-in user's own session token (lib/auth/session.ts; lab_auth re-derives the member); the Lab
// holds no storage or service key.
import { labApiUrl, labConfig } from "../../auth/config.ts";
import { sessionToken } from "../../auth/session.ts";
import type { EvaluationPort } from "./port.ts";
import { httpEvaluation } from "./http.ts";

export function labEvaluation(env: Record<string, string | undefined>): EvaluationPort | null {
  const baseUrl = labApiUrl(env, "evaluation");
  const config = labConfig(env);
  if (!baseUrl || config === null) return null;
  return httpEvaluation({ baseUrl, token: sessionToken(config) });
}
