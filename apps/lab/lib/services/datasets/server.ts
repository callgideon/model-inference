// N4: the request's datasets port. The backend URL is server-only configuration (LAB_API_URL) and the credential is the signed-in user's own session token (lib/auth/session.ts;
// the backend verifies it and re-derives the provider: WR-N-2); no storage or service key exists in the
// Lab. Unconfigured or tokenless, every call is unavailable.
import { labConfig } from "../../auth/config.ts";
import { sessionToken } from "../../auth/session.ts";
import { httpDatasets, offlineDatasets, type DatasetsPort } from "./port.ts";

export async function datasetsPort(): Promise<DatasetsPort> {
  const config = labConfig(process.env);
  if (config === null) return offlineDatasets();
  const token = await sessionToken();
  return token ? httpDatasets({ baseUrl: config.apiUrl, token }) : offlineDatasets("the session has no token");
}
