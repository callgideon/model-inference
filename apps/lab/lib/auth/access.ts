// L1: who gets a provider workspace. Pure, so it runs under node --test; guard.ts wires it to the
// request. Provider membership is the only authority (never consumer ownership or the operator bit).

export const ROLES = ["viewer", "developer", "administrator"] as const;
export type Role = (typeof ROLES)[number];
/** A provider membership as GET /lab/v1/workspaces answers it: `capabilities` is the API's set for it. */
export type Membership = { providerId: string; providerName: string; role: Role; capabilities: readonly Capability[] };
/** Who a port call acts as: always the session's workspace (lib/auth/guard.ts), never a form value. A
 * Membership is one, so pages and actions pass the workspace itself (LAB-08: replaces four copies).
 * A fake's actor may omit the API's capability set; holds() then answers from the contract table. */
export type Actor = Pick<Membership, "providerId" | "role"> & Partial<Pick<Membership, "capabilities">>;

/**
 * contracts/v2 ROLE_CAPABILITIES (apps/app/lib/contracts/v2/types.ts PROVIDER_CAPABILITIES and
 * ROLE_CAPABILITIES), copied because the Lab never imports the App (tests/l/shell L1-A10 pins the copy).
 * The one role table (LAB-07: replaces the control, evaluation and pipelines tables); the backends
 * re-check every call. `read_customer_content` is deliberately in no role's set.
 */
export const CAPABILITIES = [
  "read_aggregate_health",
  "manage_dev_deployment",
  "run_evaluation",
  "propose_publication",
  "manage_members",
  "read_customer_content",
] as const;
export type Capability = (typeof CAPABILITIES)[number];
export const ROLE_CAPABILITIES: Readonly<Record<Role, readonly Capability[]>> = Object.freeze({
  viewer: ["read_aggregate_health"],
  developer: ["read_aggregate_health", "manage_dev_deployment", "run_evaluation"],
  administrator: ["read_aggregate_health", "manage_dev_deployment", "run_evaluation", "propose_publication", "manage_members"],
});
/** AP-09: a workspace (or a port's actor) answers from the API's capability set for it; a bare role
 *  (presentation that has only the role) from the contract table above. */
export const holds = (who: Role | Actor, capability: Capability): boolean =>
  (typeof who === "string" ? ROLE_CAPABILITIES[who] : (who.capabilities ?? ROLE_CAPABILITIES[who.role])).includes(capability);
/** The read of the signed-in user's own provider memberships: a refused session is signed-out, any
 *  other failure unavailable. */
export type MembershipRead = { ok: true; memberships: Membership[] } | { ok: false; reason: "signed-out" | "unavailable" };

export type Access =
  | { kind: "signed-out" }
  | { kind: "unavailable" }
  | { kind: "denied" }
  | { kind: "select"; workspaces: Membership[] }
  | { kind: "ready"; workspace: Membership; workspaces: Membership[] };

export const ACCESS_COPY: Record<"signed-out" | "unavailable" | "denied", string> = {
  "signed-out": "Sign in to the Lab with your provider account.",
  unavailable: "Provider access could not be checked. Nothing is shown until it can be; try again shortly.",
  denied: "This account has no provider workspace. Consumer accounts use the infrx App.",
};

/** WR-L1-6: the sign-in notices, fixed: the auth server's own words are never shown. */
export const SIGN_IN_COPY: Record<"failed" | "unavailable", string> = {
  failed: "That email and password did not sign you in. Check them, or ask your provider administrator.",
  unavailable: ACCESS_COPY.unavailable,
};

/** A signed-in user's access. `selected` is the preference cookie: honoured only for a membership. */
export function providerAccess(read: MembershipRead, selected: string | undefined): Access {
  if (!read.ok) return { kind: read.reason };
  const workspaces = read.memberships;
  if (workspaces.length === 0) return { kind: "denied" };
  const workspace = workspaces.length === 1 ? workspaces[0] : workspaces.find((m) => m.providerId === selected);
  return workspace === undefined ? { kind: "select", workspaces } : { kind: "ready", workspace, workspaces };
}

/** `signedIn`: the request carries a session token; the API judges whether it is still good. */
export async function resolveAccess(deps: {
  signedIn: boolean;
  memberships: () => Promise<MembershipRead>;
  selected: string | undefined;
}): Promise<Access> {
  if (!deps.signedIn) return { kind: "signed-out" };
  return providerAccess(await deps.memberships(), deps.selected);
}

export type SessionAccess = Extract<Access, { kind: "select" | "ready" }>;

/** Signed in with at least one provider membership, selected or not. */
export function sessionAccess(access: Access): SessionAccess | null {
  return access.kind === "select" || access.kind === "ready" ? access : null;
}

export function readyWorkspace(access: Access): Membership | null {
  return access.kind === "ready" ? access.workspace : null;
}

/** The workspace a selection action may store: one of the user's own, or nothing. */
export function chooseWorkspace(access: Access, requested: unknown): Membership | null {
  return sessionAccess(access)?.workspaces.find((m) => m.providerId === requested) ?? null;
}
