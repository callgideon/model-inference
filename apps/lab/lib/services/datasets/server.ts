// N4: the request's datasets port. The backend URL is server-only configuration and the credential is
// the signed-in user's own session token (the backend verifies it and re-derives the provider: WR-N-2);
// no storage or service key exists in the Lab (lib/auth/session.ts). Unconfigured or tokenless, every
// call is unavailable.
import { labConfig } from "../../auth/config.ts";
import { sessionToken } from "../../auth/session.ts";
import { httpDatasets, offlineDatasets, type DatasetsPort } from "./port.ts";

export async function datasetsPort(): Promise<DatasetsPort> {
  const baseUrl = process.env.LAB_DATASETS_API_URL;
  const config = labConfig(process.env);
  if (!baseUrl || config === null) return offlineDatasets();
  const token = await sessionToken(config)();
  return token ? httpDatasets({ baseUrl, token }) : offlineDatasets("the session has no token");
}
