// L1: who gets a provider workspace. Pure, so it runs under node --test; guard.ts wires it to the
// request. Provider membership is the only authority (never consumer ownership or the operator bit).

export const ROLES = ["viewer", "developer", "administrator"] as const;
export type Role = (typeof ROLES)[number];
export type Membership = { providerId: string; providerName: string; role: Role };
/** The L2 read of the signed-in user's own provider memberships; `ok: false` is a failed read. */
export type MembershipRead = { ok: true; memberships: Membership[] } | { ok: false };

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

/** A signed-in user's access. `selected` is the preference cookie: honoured only for a membership. */
export function providerAccess(read: MembershipRead, selected: string | undefined): Access {
  if (!read.ok) return { kind: "unavailable" };
  const workspaces = read.memberships;
  if (workspaces.length === 0) return { kind: "denied" };
  const workspace = workspaces.length === 1 ? workspaces[0] : workspaces.find((m) => m.providerId === selected);
  return workspace === undefined ? { kind: "select", workspaces } : { kind: "ready", workspace, workspaces };
}

export async function resolveAccess(deps: {
  userId: () => Promise<string | null>;
  memberships: () => Promise<MembershipRead>;
  selected: string | undefined;
}): Promise<Access> {
  if ((await deps.userId()) === null) return { kind: "signed-out" };
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
