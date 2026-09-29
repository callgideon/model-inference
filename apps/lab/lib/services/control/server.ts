// WR-E3L-J: the control pages' adapter. The control service's base URL is server-only configuration
// (LAB_CONTROL_URL; unset = null, the port stays unavailable) and the credential is the signed-in user's
// own session token (evaluation/server.ts); the Lab holds no storage or service key.
import { labConfig } from "../../auth/config.ts";
import { sessionToken } from "../evaluation/server.ts";
import { httpControl } from "./http.ts";
import type { ControlPort } from "./port.ts";

export function labControl(env: Record<string, string | undefined>): ControlPort | null {
  const baseUrl = env.LAB_CONTROL_URL;
  const config = labConfig(env);
  if (!baseUrl || config === null) return null;
  return httpControl({ baseUrl, token: sessionToken(config) });
}
