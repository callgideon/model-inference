// WR-E3L-J: the control pages' adapter. The control service's base URL is server-only configuration
// (LAB_API_URL, else LAB_CONTROL_URL; unset = null, the port stays unavailable) and the credential is the
// signed-in user's own session token (lib/auth/session.ts); the Lab holds no storage or service key.
import { labApiUrl, labConfig } from "../../auth/config.ts";
import { sessionToken } from "../../auth/session.ts";
import { httpControl } from "./http.ts";
import type { ControlPort } from "./port.ts";

export function labControl(env: Record<string, string | undefined>): ControlPort | null {
  const baseUrl = labApiUrl(env, "control");
  const config = labConfig(env);
  if (!baseUrl || config === null) return null;
  return httpControl({ baseUrl, token: sessionToken(config) });
}
