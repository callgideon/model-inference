// L1 adapter over AP-01's membership read, GET /lab/v1/workspaces (replaces the direct
// lab_provider_memberships RPC, AP-09 09c): the API takes the identity from the session and judges
// currency on its own clock, so nothing but the session's token is sent. Each workspace carries the
// API's capability set for its role. Any bad row fails the read closed.
import type { Availability, LabApi } from "../api/index.ts";
import { UUID_ANY_RE as UUID } from "../services/shapes.ts";
import { CAPABILITIES, ROLES, type Membership, type MembershipRead, type Role } from "./access.ts";

const UNAVAILABLE: MembershipRead = { ok: false, reason: "unavailable" };

/** The API's page of workspaces as memberships. A capability the Lab does not know gates nothing
 *  here, so it is dropped (a newer server is not a malformed one). */
export function parseWorkspaces(page: unknown): MembershipRead {
  const data = (page as { data?: unknown } | null)?.data;
  if (!Array.isArray(data)) return UNAVAILABLE;
  const memberships: Membership[] = [];
  for (const row of data) {
    const { provider_org_id: id, provider_name: name, role, capabilities } = (row ?? {}) as Record<string, unknown>;
    if (typeof id !== "string" || !UUID.test(id)) return UNAVAILABLE;
    if (typeof name !== "string" || name === "") return UNAVAILABLE;
    if (!ROLES.includes(role as Role)) return UNAVAILABLE;
    if (!Array.isArray(capabilities) || !capabilities.every((c) => typeof c === "string")) return UNAVAILABLE;
    if (memberships.some((m) => m.providerId === id)) return UNAVAILABLE;
    const held = CAPABILITIES.filter((c) => capabilities.includes(c));
    memberships.push({ providerId: id, providerName: name, role: role as Role, capabilities: held });
  }
  return { ok: true, memberships };
}

export async function readMemberships(api: LabApi): Promise<MembershipRead> {
  const answer = await api.call("get", "/lab/v1/workspaces");
  if (answer.ok) return parseWorkspaces(answer.data);
  return answer.error.status === 401 ? { ok: false, reason: "signed-out" } : UNAVAILABLE;
}

const STATES: readonly string[] = ["configured", "disabled", "unavailable", "unknown"];
export type Features = { ok: true; features: Record<string, Availability> } | { ok: false };

/** AP-09 09e: the workspace's Lab features as the API states them (GET /lab/v1/capabilities). A failed
 *  or unreadable read is `{ ok: false }`, for the page to show as unavailable - never as disabled. */
export async function workspaceFeatures(api: LabApi, w: Pick<Membership, "providerId">): Promise<Features> {
  const answer = await api.call("get", "/lab/v1/capabilities", { query: { provider_org_id: w.providerId } });
  const features = answer.ok ? (answer.data as { features?: unknown } | null)?.features : null;
  if (typeof features !== "object" || features === null) return { ok: false };
  const readable = Object.values(features).every((f) => STATES.includes((f as { state?: string } | null)?.state as string));
  return readable ? { ok: true, features: features as Record<string, Availability> } : { ok: false };
}
