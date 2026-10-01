// L1 adapter over THE membership read (R156): 0030's public.lab_provider_memberships() on the user's
// own session. The database takes the identity from auth.uid() and judges currency on its own clock,
// the same set L2's port answers (apps/infrx-api/tests/d/test_l2sql_self.py), so no identity is ever
// sent. Any bad row fails the read closed.
import { UUID_ANY_RE as UUID } from "../services/shapes.ts";
import { ROLES, type Membership, type MembershipRead, type Role } from "./access.ts";

export const MEMBERSHIPS_RPC = "lab_provider_memberships";
export type RpcClient = { rpc: (name: string) => PromiseLike<{ data: unknown; error: unknown }> };


export function parseMemberships(data: unknown): MembershipRead {
  if (!Array.isArray(data)) return { ok: false };
  const memberships: Membership[] = [];
  for (const row of data) {
    const { provider_org_id: id, provider_name: name, role } = (row ?? {}) as Record<string, unknown>;
    if (typeof id !== "string" || !UUID.test(id)) return { ok: false };
    if (typeof name !== "string" || name === "") return { ok: false };
    if (!ROLES.includes(role as Role)) return { ok: false };
    if (memberships.some((m) => m.providerId === id)) return { ok: false };
    memberships.push({ providerId: id, providerName: name, role: role as Role });
  }
  return { ok: true, memberships };
}

export async function readMemberships(client: RpcClient): Promise<MembershipRead> {
  try {
    const { data, error } = await client.rpc(MEMBERSHIPS_RPC);
    return error ? { ok: false } : parseMemberships(data);
  } catch {
    return { ok: false };
  }
}
